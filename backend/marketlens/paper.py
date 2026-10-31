import copy
import math
import uuid

from .calendars import latest_closed, session_times
from .db import Dataset, Evaluation, PaperAccount, PaperEvent, Signal, append_event, latest_dataset, rules
from .domain import RULE_VERSION, digest, now_utc, parse_time, stamp
from .execution import add_order, equity, fill_cost, fx_rate, initial_state, mark_curve, process_bar
from .metrics import metrics
from .research import finite_row, indicator_frame, tradable_bars
from .strategies import detected, trade_plan


def record_signals(service, candidates):
    recorded = stamp()
    with service.db.session() as s:
        account = s.get(PaperAccount, "forward")
        state = copy.deepcopy(account.state)
        for c in candidates:
            ds = s.get(Dataset, c["dataset_id"])
            if not ds or ds.mode not in {"yahoo", "eodhd"} or ds.mode != service.settings.data_mode or not c["qualified"]:
                continue
            # Frozen account strategy, costs and risk rules; settings changes never rewrite it.
            params = account.config["strategy"]
            if params["strategy"] not in c["setups"]:
                continue
            expected = latest_closed(c["market"], lag_minutes=service.settings.provider_lag_minutes)
            if c["asof"] < expected or parse_time(ds.fetched_at) > parse_time(recorded):
                continue
            if session_times(c["market"], c["asof"])[1] > parse_time(ds.fetched_at):
                continue
            # Never enroll the same market setup again after a vendor revision or restart.
            dedupe = digest({"account": "forward", "symbol": c["symbol"], "asof": c["asof"], "strategy": params, "rule": RULE_VERSION})
            if s.query(Signal).filter_by(dedupe=dedupe).first():
                continue
            if any(o["symbol"] == c["symbol"] and o["status"] in {"pending", "filled", "partially_filled"} for o in state["orders"]):
                continue
            from .domain import StrategyParams
            row = finite_row(indicator_frame(ds.payload, params["lookback"]).iloc[-1])
            if params["strategy"] not in detected(row):
                continue
            plan = trade_plan(row, StrategyParams.model_validate(params))
            identifier = str(uuid.uuid4())
            payload = {"id": identifier, "symbol": c["symbol"], "market": c["market"], "currency": c["currency"], "strategy": params["strategy"], "asof": c["asof"], "recorded_at": recorded,
                       "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"], "rule_version": RULE_VERSION,
                       "dataset_id": ds.id, "dataset_hash": ds.content_hash, "data_fetched_at": ds.fetched_at, "indicators": row,
                       "candidate": c, "config": account.config, "kind": "forward", "available_news": c.get("news", [])}
            s.add(Signal(id=identifier, dedupe=dedupe, account_id="forward", symbol=c["symbol"], recorded_at=recorded, kind="forward", payload=payload, hash=digest(payload)))
            add_order(state, payload)
            state.setdefault("benchmark_start", recorded)
            append_event(s, "forward", f"signal:{identifier}", {"type": "signal_recorded", "signal_id": identifier, "signal_hash": digest(payload)})
        account.state = state


def evaluate(service):
    if service.settings.data_mode not in {"yahoo", "eodhd"}:
        return {"evaluated": 0, "notice": "Fixture observations cannot evaluate the forward account"}
    with service.db.session() as s:
        account = s.get(PaperAccount, "forward")
        state = copy.deepcopy(account.state)
        config = account.config
        fxds = latest_dataset(s, "USDAUD.FOREX", service.settings.data_mode)
        rates = fxds.payload["rates"] if fxds else []
        schedule = []
        symbols = {o["symbol"] for o in state["orders"] if o["status"] in {"pending", "filled", "partially_filled"}}
        symbols.update(r["symbol"] for r in state["receivables"])
        preflight_failures = []
        cutoff = None
        for symbol in symbols:
            ds = latest_dataset(s, symbol, service.settings.data_mode)
            if not ds:
                preflight_failures.append({"symbol": symbol, "reason": "Active symbol feed unavailable"})
                continue
            market = service.instrument(symbol).market
            first = min(parse_time(o["recorded_at"]) for o in state["orders"] if o["symbol"] == symbol)
            observed = {b["date"] for b in ds.payload["bars"]}
            from .calendars import sessions
            expected = sessions(market, first.date().isoformat(), ds.payload["bars"][-1]["date"])
            holes = [day for day in expected if session_times(market, day)[0] > first and day not in observed]
            if holes:
                preflight_failures.append({"symbol": symbol, "reason": f"Missing eligible sessions: {', '.join(holes[:5])}"})
            close = session_times(market, ds.payload["bars"][-1]["date"])[1]
            cutoff = min(cutoff, close) if cutoff else close
        if preflight_failures:
            for failure in preflight_failures:
                append_event(s, "forward", f"data-failure:{digest(failure)}", {"type": "execution_failure", **failure})
            return {"evaluated": 0, "failures": preflight_failures}
        for symbol in symbols:
            ds = latest_dataset(s, symbol, service.settings.data_mode)
            inst = service.instrument(symbol)
            if not ds or parse_time(ds.fetched_at) > now_utc():
                continue
            origins = [o["recorded_at"] for o in state["orders"] if o["symbol"] == symbol]
            for bar in tradable_bars(ds.payload):
                op, close = session_times(inst.market, bar["date"])
                if op <= min(parse_time(x) for x in origins) or close > now_utc() or cutoff and close > cutoff:
                    continue
                if state.get("last_evaluated_close") and close < parse_time(state["last_evaluated_close"]):
                    # Late revisions cannot rewrite an already committed portfolio timeline.
                    if not s.query(Evaluation).filter_by(account_id="forward", symbol=symbol, session=bar["date"]).first():
                        append_event(s, "forward", f"late:{symbol}:{bar['date']}", {"type": "execution_failure", "symbol": symbol, "date": bar["date"], "reason": "Late observation predates committed portfolio timeline; manual investigation required"})
                    continue
                if not s.query(Evaluation).filter_by(account_id="forward", symbol=symbol, session=bar["date"]).first():
                    schedule.append((op, symbol, bar, ds))
        evaluated, failures = 0, []
        for op, symbol, bar, ds in sorted(schedule, key=lambda x: (x[0], x[1])):
            inst = service.instrument(symbol)
            try:
                fx = fx_rate(rates, bar["date"], ds.payload["currency"])
            except ValueError as exc:
                failures.append({"symbol": symbol, "session": bar["date"], "reason": str(exc)})
                append_event(s, "forward", f"fx-unavailable:{symbol}:{bar['date']}", {"type": "execution_failure", "date": bar["date"], "symbol": symbol, "reason": str(exc)})
                # Do not process newer observations around a hole; retry once repaired.
                break
            events = process_bar(state, symbol, bar, fx, config["costs"], config["portfolio"], ds.payload["actions"], eligible=lambda o, op=op: op > parse_time(o["recorded_at"]) and bar["date"] > o["asof"])
            for n, event in enumerate(events):
                append_event(s, "forward", f"bar:{symbol}:{bar['date']}:{n}", {**event, "dataset_id": ds.id, "fx_dataset_id": fxds.id if fxds else None, "bar_open_at": stamp(op)})
            s.add(Evaluation(account_id="forward", symbol=symbol, session=bar["date"]))
            append_event(s, "forward", f"evaluated:{symbol}:{bar['date']}", {"type": "bar_evaluated", "symbol": symbol, "date": bar["date"], "dataset_id": ds.id, "fx_dataset_id": fxds.id if fxds else None})
            mark_curve(state, bar["date"])
            state["last_evaluated_close"] = stamp(session_times(inst.market, bar["date"])[1])
            evaluated += 1
        # A contemporaneous 50/50 market ETF baseline, separately simulated with the same costs.
        if state.get("benchmark_start"):
            baseline = state.setdefault("benchmark", initial_state(config["portfolio"]["starting_balance"]))
            benchmark_schedule = []
            for symbol in ("STW.AU", "SPY.US"):
                ds = latest_dataset(s, symbol, service.settings.data_mode)
                if not ds:
                    continue
                market = "ASX" if symbol.endswith(".AU") else "US"
                for bar in tradable_bars(ds.payload):
                    op, close = session_times(market, bar["date"])
                    if op <= parse_time(state["benchmark_start"]) or close > now_utc():
                        continue
                    if s.query(Evaluation).filter_by(account_id="forward", symbol=f"benchmark:{symbol}", session=bar["date"]).first():
                        continue
                    benchmark_schedule.append((op, symbol, bar, ds, market))
            for op, symbol, bar, ds, market in sorted(benchmark_schedule, key=lambda x: (x[0], x[1])):
                close = session_times(market, bar["date"])[1]
                if baseline.get("last_evaluated_close") and close < parse_time(baseline["last_evaluated_close"]):
                    continue
                try:
                    fx = fx_rate(rates, bar["date"], ds.payload["currency"])
                except ValueError:
                    break
                if not any(o["symbol"] == symbol for o in baseline["orders"]):
                    budget = min(baseline["cash"], config["portfolio"]["starting_balance"] / 2)
                    c = config["costs"]
                    unit = fill_cost(bar["open"], 1, fx, ds.payload["currency"], "buy", c)
                    qty = min(math.floor(bar["volume"] * c["participation_rate"]), max(0, math.floor((budget - c["minimum_fee_aud"]) / (unit["price"] * fx * (1 + (c["commission_bps"] + (c["fx_fee_bps"] if ds.payload["currency"] == "USD" else 0)) / 10000)))))
                    if qty:
                        fill = fill_cost(bar["open"], qty, fx, ds.payload["currency"], "buy", c)
                        baseline["cash"] -= fill["cash"]
                        for k in baseline["costs"]:
                            baseline["costs"][k] += fill[k]
                        baseline["orders"].append({"id": symbol, "symbol": symbol, "status": "filled"})
                        baseline["positions"][symbol] = {"id": symbol, "symbol": symbol, "strategy": "buy_hold", "market": market, "currency": ds.payload["currency"], "quantity": qty, "entry_date": bar["date"], "entry_price": fill["price"], "entry_fx": fx, "entry_cash": fill["cash"], "initial_risk_aud": fill["cash"], "stop": 0, "target": 1e100, "dividends_aud": 0}
                process_bar(baseline, symbol, bar, fx, config["costs"], config["portfolio"], ds.payload["actions"])
                mark_curve(baseline, bar["date"])
                baseline["last_evaluated_close"] = stamp(close)
                s.add(Evaluation(account_id="forward", symbol=f"benchmark:{symbol}", session=bar["date"]))
                append_event(s, "forward", f"benchmark:{symbol}:{bar['date']}", {"type": "benchmark_observed", "symbol": symbol, "date": bar["date"], "dataset_id": ds.id, "fx_dataset_id": fxds.id if fxds else None})
        account.state = state
    return {"evaluated": evaluated, "failures": failures}


def replay(service, symbol="BHP.AU"):
    """Explicit historical fixture replay. Never touches the forward account."""
    if service.settings.data_mode != "demo":
        raise ValueError("Demonstration replay is available only in DATA_MODE=demo")
    inst = service.instrument(symbol)
    with service.db.session() as s:
        ds = latest_dataset(s, symbol, "demo")
        fxds = latest_dataset(s, "USDAUD.FOREX", "demo")
        config = rules(s).model_dump()
        if not ds or not fxds:
            raise ValueError("Load fixture data before replay")
        payload = ds.payload
        frame = indicator_frame(payload, config["strategy"]["lookback"])
        state = initial_state(config["portfolio"]["starting_balance"])
        identifier = f"replay-{uuid.uuid4()}"
        s.add(PaperAccount(id=identifier, kind="replay", created_at=stamp(), config=config, state=state))
        from .domain import StrategyParams
        for bar in tradable_bars(payload):
            day = bar["date"]
            if day < "2024-01-01":
                continue
            fx = fx_rate(fxds.payload["rates"], day, payload["currency"])
            events = process_bar(state, symbol, bar, fx, config["costs"], config["portfolio"], payload["actions"])
            for n, event in enumerate(events):
                append_event(s, identifier, f"{identifier}:{day}:{n}", {**event, "kind": "historical_fixture_replay", "dataset_id": ds.id})
            row = finite_row(frame.loc[day])
            strategy = config["strategy"]["strategy"]
            if strategy in detected(row) and not state["positions"] and not any(o["status"] == "pending" for o in state["orders"]):
                plan = trade_plan(row, StrategyParams.model_validate(config["strategy"]))
                sigid = str(uuid.uuid4())
                signal = {"id": sigid, "symbol": symbol, "strategy": strategy, "market": inst.market, "currency": payload["currency"], "asof": day, "recorded_at": stamp(), "kind": "replay", "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"], "dataset_id": ds.id, "config": config, "indicators": row}
                s.add(Signal(id=sigid, dedupe=f"{identifier}:{day}", account_id=identifier, symbol=symbol, recorded_at=stamp(), kind="replay", payload=signal, hash=digest(signal)))
                add_order(state, signal)
                append_event(s, identifier, f"signal:{sigid}", {"type": "replay_signal", "signal_id": sigid})
            mark_curve(state, day)
        s.get(PaperAccount, identifier).state = copy.deepcopy(state)
    return account_view(service, identifier)


def cancel(service, signal_id):
    with service.db.session() as s:
        sig = s.get(Signal, signal_id)
        if not sig:
            raise ValueError("Signal not found")
        account = s.get(PaperAccount, sig.account_id)
        state = copy.deepcopy(account.state)
        order = next(o for o in state["orders"] if o["id"] == signal_id)
        if order["status"] != "pending":
            raise ValueError("Only a pending simulated entry can be cancelled")
        order["status"] = "cancelled"
        append_event(s, account.id, f"cancel:{signal_id}", {"type": "cancelled", "signal_id": signal_id, "reason": "User cancelled pending simulated entry; original signal retained"})
        account.state = state
    return {"status": "cancelled"}


def account_view(service, account_id="forward", full=False):
    with service.db.session() as s:
        account = s.get(PaperAccount, account_id)
        if not account:
            raise ValueError("Paper account not found")
        state = account.state
        signals = s.query(Signal).filter_by(account_id=account_id).all()
        events = s.query(PaperEvent).filter_by(account_id=account_id).order_by(PaperEvent.id).all()
        evals = s.query(Evaluation).filter_by(account_id=account_id).all()
        evaluable = 0
        for sig in signals:
            if sig.kind == "forward" and any(e.symbol == sig.symbol and session_times(service.instrument(sig.symbol).market, e.session)[0] > parse_time(sig.recorded_at) for e in evals):
                evaluable += 1
        terminal = {"stopped", "target_hit", "expired", "rejected", "cancelled"}
        closed = [o for o in state["orders"] if o["status"] in terminal]
        perf = metrics(state["curve"], state["trades"], account.config["portfolio"]["starting_balance"])
        # Marks may stay at their last eligible observation; they are never synthesized.
        current_equity = equity(state)
        breakdown = []
        for key in ("strategy", "market"):
            values = sorted(set(o[key] for o in state["orders"] if key in o))
            for value in values:
                trades = [t for t in state["trades"] if t[key] == value]
                related = [o for o in state["orders"] if o.get(key) == value]
                summary = metrics([], trades, account.config["portfolio"]["starting_balance"])
                breakdown.append({"group": key, "value": value, "signals": len(related), "closed_trades": len(trades), "win_rate": summary["win_rate"], "win_rate_95_interval": summary["win_rate_95_interval"], "expectancy_aud": summary["expectancy_aud"], "profit_factor": summary["profit_factor"], "failures": sum(o["status"] in {"rejected", "expired"} for o in related)})
        chain = "0" * 64
        valid = True
        for e in events:
            valid = valid and e.previous_hash == chain and e.hash == digest({"previous": chain, "key": e.key, "created_at": e.created_at, "payload": e.payload})
            chain = e.hash
        valid = valid and all(sig.hash == digest(sig.payload) for sig in signals)
        baseline = state.get("benchmark")
        benchmark_return = equity(baseline) / account.config["portfolio"]["starting_balance"] - 1 if baseline and baseline["positions"] else None
        replays = [{"id": a.id, "created_at": a.created_at} for a in s.query(PaperAccount).filter_by(kind="replay").order_by(PaperAccount.created_at.desc()).limit(10)]
        return {"id": account.id, "kind": account.kind, "created_at": account.created_at, "config": account.config, "state": state, "metrics": perf,
                "cash": state["cash"], "equity": current_equity, "realised_pnl": sum(t["pnl_aud"] for t in state["trades"]), "unrealised_pnl": sum(p["quantity"] * state["marks"].get(p["symbol"], {"price": p["entry_price"], "fx": p["entry_fx"]})["price"] * state["marks"].get(p["symbol"], {"fx": p["entry_fx"]})["fx"] - p["entry_cash"] for p in state["positions"].values()),
                "milestone": {"generated": len(signals) if account.kind == "forward" else 0, "evaluable": evaluable, "resolved": len(closed) if account.kind == "forward" else 0, "target": 100, "complete": evaluable >= 100, "notice": "Only genuinely forward-recorded real-data signals with later eligible sessions count. 100 is not statistical proof."},
                "benchmark_return": benchmark_return, "benchmark_relative_return": (current_equity / account.config["portfolio"]["starting_balance"] - 1 - benchmark_return) if benchmark_return is not None else None,
                "benchmark_assumptions": "50/50 initial AUD allocations to STW.AU and SPY.US at first eligible sessions after first forward signal; same fees/FX, residual cash, no rebalancing. Dates and stale marks visible in state. Contextual benchmark; strategy market weights may differ.",
                "breakdown": breakdown, "audit_valid": valid, "events": [{"id": e.id, "created_at": e.created_at, "previous_hash": e.previous_hash, "key": e.key, "hash": e.hash, **e.payload} for e in (events if full else events[-250:])], "event_count": len(events), "replays": replays,
                "signals": [{"id": sig.id, "recorded_at": sig.recorded_at, "hash": sig.hash, **sig.payload} for sig in (signals if full else signals[-200:])], "mark_notice": "Unrealised values use last eligible observed close and prior-date FX; inspect mark dates for staleness. Marks are not executable quotes."}
