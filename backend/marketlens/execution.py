"""Pure long-only EOD simulator. Contains no exchange or broker connectivity."""
import copy
import math
from datetime import date

from .domain import Costs, PortfolioRules


def initial_state(balance=10000):
    return {"cash": balance, "initial_balance": balance, "orders": [], "positions": {}, "trades": [], "curve": [], "receivables": [],
            "costs": {"commission": 0.0, "fx": 0.0, "spread": 0.0, "slippage": 0.0}, "marks": {}, "applied_actions": []}


def fx_rate(rates, day, currency):
    if currency == "AUD":
        return 1.0
    prior = [r for r in rates if r["date"] < day]
    if not prior:
        raise ValueError("Prior-date AUD per USD FX rate unavailable")
    r = max(prior, key=lambda x: x["date"])
    if (date.fromisoformat(day) - date.fromisoformat(r["date"])).days > 7:
        raise ValueError("FX rate stale by more than seven days")
    return r["rate"]


def fill_cost(raw, qty, fx, currency, side, costs):
    c = costs if isinstance(costs, Costs) else Costs.model_validate(costs)
    direction = 1 if side == "buy" else -1
    spread = raw * qty * fx * c.spread_bps / 20000
    slip = raw * qty * fx * c.slippage_bps / 10000
    price = raw * (1 + direction * (c.spread_bps / 2 + c.slippage_bps) / 10000)
    notional = price * qty * fx
    commission = max(c.minimum_fee_aud, notional * c.commission_bps / 10000)
    fx_fee = notional * c.fx_fee_bps / 10000 if currency == "USD" else 0
    cash = notional + commission + fx_fee if side == "buy" else notional - commission - fx_fee
    return {"price": price, "notional": notional, "commission": commission, "fx": fx_fee,
            "spread": spread, "slippage": slip, "cash": cash, "fx_rate": fx, "quantity": qty, "raw_price": raw}


def equity(state):
    holdings = sum(p["quantity"] * state["marks"].get(p["symbol"], {"price": p["entry_price"], "fx": p["entry_fx"]})["price"] * state["marks"].get(p["symbol"], {"fx": p["entry_fx"]})["fx"] for p in state["positions"].values())
    return state["cash"] + holdings + sum(r["amount_aud"] for r in state["receivables"])


def _add_costs(state, fill):
    for k in state["costs"]:
        state["costs"][k] += fill[k]


def add_order(state, signal):
    if not any(o["id"] == signal["id"] for o in state["orders"]):
        state["orders"].append({**copy.deepcopy(signal), "status": "pending", "evaluations": 0})


def process_bar(state, symbol, bar, fx, costs, portfolio, actions=None, eligible=None):
    """Mutates a detached projection; caller persists it with events in one transaction.

    Stop-first OHLC ambiguity. Targets are never credited on an entry bar. Entry
    remainder is cancelled (IOC), protective exits are full quantity at adverse
    prices. This last assumption is visible and not a claim of executable depth.
    """
    c = costs if isinstance(costs, Costs) else Costs.model_validate(costs)
    p = portfolio if isinstance(portfolio, PortfolioRules) else PortfolioRules.model_validate(portfolio)
    day = bar["date"]
    events = []
    eligible = eligible or (lambda order: day > order["asof"])
    for action in actions or []:
        action_key = f"{symbol}:{action}"
        if action["date"] != day or action_key in state["applied_actions"]:
            continue
        state["applied_actions"].append(action_key)
        for pos in state["positions"].values():
            if pos["symbol"] != symbol:
                continue
            if action["kind"] == "split":
                ratio = action["ratio"]
                pos["quantity"] *= ratio
                for key in ("entry_price", "stop", "target"):
                    pos[key] /= ratio
                events.append({"type": "split", "signal_id": pos["id"], "date": day, "ratio": ratio, "fractional_share_policy": "retained synthetically"})
            elif action["kind"] == "dividend":
                if action.get("currency") and action["currency"] != pos["currency"]:
                    raise ValueError("Dividend currency differs from listing; explicit conversion data required")
                pay = action.get("payment_date")
                state["receivables"].append({"amount_aud": pos["quantity"] * action["amount"] * fx, "pay_date": pay or day, "signal_id": pos["id"], "estimated_payment": not bool(pay), "symbol": symbol})
                pos["dividends_aud"] += pos["quantity"] * action["amount"] * fx
                events.append({"type": "dividend_entitlement", "date": day, "signal_id": pos["id"], "amount_aud": pos["quantity"] * action["amount"] * fx, "payment_date": pay or day, "assumption": "Ex-date FX locks AUD entitlement; ex-date cash if payment date absent; no tax or franking"})
        if action["kind"] == "split":
            # Original signals remain immutable; pending executable projections rebase explicitly.
            for o in state["orders"]:
                if o["symbol"] == symbol and o["status"] == "pending":
                    for key in ("entry", "stop", "target"):
                        o[key] /= action["ratio"]
                    events.append({"type": "pending_split_adjustment", "signal_id": o["id"], "date": day, "ratio": action["ratio"]})
    for rec in list(state["receivables"]):
        if rec["pay_date"] <= day:
            state["cash"] += rec["amount_aud"]
            state["receivables"].remove(rec)
            events.append({"type": "dividend_payment", "date": day, **rec})

    def close_position(pos, raw_price, reason):
        filled = fill_cost(raw_price, pos["quantity"], fx, pos["currency"], "sell", c)
        _add_costs(state, filled)
        state["cash"] += filled["cash"]
        pnl = filled["cash"] - pos["entry_cash"] + pos["dividends_aud"]
        trade = {**pos, "exit_date": day, "exit_price": filled["price"], "exit_fx": fx, "exit_cash": filled["cash"], "pnl_aud": pnl, "r_multiple": pnl / pos["initial_risk_aud"] if pos["initial_risk_aud"] else None, "reason": reason, "exit_fill": filled}
        state["trades"].append(trade)
        del state["positions"][pos["id"]]
        next(o for o in state["orders"] if o["id"] == pos["id"])["status"] = reason
        events.append({"type": reason, "signal_id": pos["id"], "date": day, "fill": filled, "pnl_aud": pnl})

    for pos in list(state["positions"].values()):
        if pos["symbol"] != symbol:
            continue
        # Stop gets priority when both protective levels are touched.
        if bar["open"] <= pos["stop"]:
            close_position(pos, bar["open"], "stopped")
        elif bar["low"] <= pos["stop"]:
            close_position(pos, pos["stop"], "stopped")
        elif bar["high"] >= pos["target"]:
            close_position(pos, pos["target"], "target_hit")

    for order in state["orders"]:
        if order["symbol"] != symbol or order["status"] != "pending" or not eligible(order):
            continue
        order["evaluations"] += 1
        if order["evaluations"] > p.expiry_sessions:
            order["status"] = "expired"
            events.append({"type": "expired", "signal_id": order["id"], "date": day})
            continue
        if bar["high"] < order["entry"]:
            if order["evaluations"] == p.expiry_sessions:
                order["status"] = "expired"
                events.append({"type": "expired", "signal_id": order["id"], "date": day})
            continue
        raw = max(bar["open"], order["entry"])
        eq = equity(state)
        per_share_risk = (raw - order["stop"]) * fx
        friction_risk = raw * fx * (c.commission_bps * 2 + c.spread_bps + c.slippage_bps * 2 + (c.fx_fee_bps * 2 if order["currency"] == "USD" else 0)) / 10000
        risk_capacity = eq * p.max_portfolio_risk - sum(pos["initial_risk_aud"] for pos in state["positions"].values())
        desired = max(0, math.floor((min(eq * p.risk_per_trade, risk_capacity) - c.minimum_fee_aud * 2) / max(1e-9, per_share_risk + friction_risk)))
        one = fill_cost(raw, 1, fx, order["currency"], "buy", c)
        desired = min(desired, max(0, math.floor(eq * p.max_position_fraction / (one["price"] * fx))), max(0, math.floor((state["cash"] - c.minimum_fee_aud) / (one["price"] * fx * (1 + (c.commission_bps + (c.fx_fee_bps if order["currency"] == "USD" else 0)) / 10000)))))
        quantity = min(desired, math.floor(bar["volume"] * c.participation_rate))
        reason = None
        if raw >= order["target"]:
            reason = "Opening gap reached original target; positive planned reward unavailable"
        if len(state["positions"]) >= p.max_positions:
            reason = "Maximum concurrent positions"
        if any(pos["symbol"] == symbol for pos in state["positions"].values()):
            reason = "Existing position in symbol"
        if quantity <= 0:
            reason = "Insufficient cash, risk capacity, allocation or eligible volume"
        if reason:
            order["status"] = "rejected"
            order["rejection"] = reason
            events.append({"type": "rejected", "signal_id": order["id"], "date": day, "reason": reason})
            continue
        filled = fill_cost(raw, quantity, fx, order["currency"], "buy", c)
        if filled["cash"] > state["cash"] + 1e-8:
            raise ValueError("Cash constraint invariant violated")
        state["cash"] -= filled["cash"]
        _add_costs(state, filled)
        partial = quantity < desired
        order["status"] = "partially_filled" if partial else "filled"
        order["filled_quantity"] = quantity
        order["cancelled_remainder"] = desired - quantity
        pos = {"id": order["id"], "symbol": symbol, "strategy": order["strategy"], "market": order["market"], "currency": order["currency"], "quantity": quantity, "entry_date": day, "entry_price": filled["price"], "entry_fx": fx, "entry_cash": filled["cash"], "initial_risk_aud": quantity * per_share_risk, "stop": order["stop"], "target": order["target"], "dividends_aud": 0, "entry_fill": filled}
        state["positions"][order["id"]] = pos
        events.append({"type": order["status"], "signal_id": order["id"], "date": day, "fill": filled, "cancelled_ioc_remainder": desired - quantity})
        if bar["low"] <= pos["stop"]:
            close_position(pos, min(bar["open"], pos["stop"]) if bar["open"] >= order["entry"] else pos["stop"], "stopped")
    state["marks"][symbol] = {"price": bar["close"], "fx": fx, "date": day}
    return events


def mark_curve(state, day):
    eq = equity(state)
    exposure = sum(p["quantity"] * state["marks"].get(p["symbol"], {"price": p["entry_price"], "fx": p["entry_fx"]})["price"] * state["marks"].get(p["symbol"], {"fx": p["entry_fx"]})["fx"] for p in state["positions"].values())
    peak = max([eq, state.get("initial_balance", eq)] + [r["equity"] for r in state["curve"]])
    row = {"date": day, "equity": eq, "cash": state["cash"], "drawdown": (eq / peak - 1) if peak else 0, "exposure": exposure / eq if eq else 0}
    if state["curve"] and state["curve"][-1]["date"] == day:
        state["curve"][-1] = row
    else:
        state["curve"].append(row)
