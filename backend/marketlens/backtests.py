import hashlib
from pathlib import Path

import backtrader as bt
import pandas as pd

from .domain import RULE_VERSION, StrategyParams, digest
from .execution import add_order, equity, fill_cost, fx_rate, initial_state, mark_curve, process_bar
from .metrics import metrics
from .research import finite_row, indicator_frame, tradable_bars
from .strategies import detected, trade_plan

ASSUMPTIONS = [
    "Historical simulation, not verified profitable performance. Long-only EOD buy-stop entries after signal close; no same-close fills.",
    "Backtrader drives chronological events; an independently tested AUD ledger supplies costs, FX, cash constraints and conservative OHLC fills. No broker connectivity.",
    "Raw OHLC executions; split adjustments apply only on their effective date. Adjusted close is never a tradable price. Provider volume reconstructed using disclosed split factors.",
    "Stop-first when stop and target are touched; no target credited on entry bar. Stop gaps fill at adverse open. No favorable target gap improvement.",
    "Entry participation cap; unfilled entry remainder cancelled IOC. Protective exits assume full liquidity with spread/slippage; no order-book depth or queue modelling.",
    "Dividends: ex-date entitlement, paid on provided payment date (ex-date if absent), ex-date FX locked, no tax/franking. Fractional split shares retained synthetically.",
    "Prior-date AUD per USD FX with ≤7-calendar-day age; both benchmark and strategy use identical costs and FX. No terminal liquidation: open holdings marked, exit costs not yet charged.",
    "Buy-and-hold benchmark invests available cash at the first test-session open (subject to participation), rather than matching strategy exposure.",
    "Historical data downloaded now is not a historical availability archive. Provider corrections and announcement timestamps may be unavailable. Explicitly not a point-in-time verified population study.",
    "Default universe is a current illustrative selection and excludes unknown delistings. Results are conditional single-instrument studies with survivorship bias. Missing sessions fail a run; unexplained disappearance is never valued at a fabricated delisting price.",
    "Train/validation/test are chronological independent portfolios, with earlier bars used only for indicator warmup. Sensitivity and walk-forward selection use training data only. Repeated holdout viewing invalidates an untouched claim.",
]


def code_version():
    h = hashlib.sha256()
    for path in sorted(Path(__file__).parent.glob("*.py")):
        h.update(path.name.encode())
        h.update(path.read_bytes())
    return h.hexdigest()


def simulate(payload, instrument, fx_payload, params, costs, portfolio, start, end, benchmark_payload=None, buy_hold=False):
    params = StrategyParams.model_validate(params)
    frame = indicator_frame(payload, params.lookback)
    raw = {b["date"]: b for b in tradable_bars(payload)}
    selected = frame.loc[(frame.index >= start) & (frame.index <= end)]
    if len(selected) < 2:
        raise ValueError("At least two eligible test sessions required")
    state = initial_state(portfolio["starting_balance"])
    initial_day = (pd.Timestamp(selected.index[0]) - pd.Timedelta(days=1)).date().isoformat()
    mark_curve(state, initial_day)
    events = []
    currency = payload["currency"]

    class ResearchClock(bt.Strategy):
        def next(self):
            day = self.datas[0].datetime.date(0).isoformat()
            bar = raw[day]
            fx = fx_rate(fx_payload.get("rates", []), day, currency)
            if buy_hold and len(state["curve"]) == 1:
                import math
                qty = min(math.floor(bar["volume"] * costs["participation_rate"]), math.floor((state["cash"] - costs["minimum_fee_aud"]) / (bar["open"] * fx * (1 + (costs["spread_bps"] / 2 + costs["slippage_bps"] + costs["commission_bps"] + (costs["fx_fee_bps"] if currency == "USD" else 0)) / 10000))))
                if qty <= 0:
                    raise ValueError("Benchmark cannot purchase one share under the same cash/cost assumptions")
                fill = fill_cost(bar["open"], qty, fx, currency, "buy", costs)
                state["cash"] -= fill["cash"]
                state["positions"]["benchmark"] = {"id": "benchmark", "symbol": instrument.symbol, "strategy": "buy_hold", "market": instrument.market, "currency": currency, "quantity": qty, "entry_date": day, "entry_price": fill["price"], "entry_fx": fx, "entry_cash": fill["cash"], "initial_risk_aud": fill["cash"], "stop": 0, "target": 1e100, "dividends_aud": 0, "entry_fill": fill}
                state["orders"].append({"id": "benchmark", "status": "filled", "symbol": instrument.symbol})
                for k in state["costs"]:
                    state["costs"][k] += fill[k]
            events.extend(process_bar(state, instrument.symbol, bar, fx, costs, portfolio, payload.get("actions", [])))
            mark_curve(state, day)
            if not buy_hold:
                row = finite_row(frame.loc[day])
                if params.strategy in detected(row) and not state["positions"] and not any(o["status"] == "pending" for o in state["orders"]):
                    plan = trade_plan(row, params)
                    signal = {"id": f"{instrument.symbol}:{day}:{params.strategy}", "symbol": instrument.symbol, "asof": day, "strategy": params.strategy, "market": instrument.market, "currency": currency, "entry": plan["entry"], "stop": plan["stop"], "target": plan["target"]}
                    add_order(state, signal)

    feed = selected[["open", "high", "low", "close", "volume"]].copy()
    feed.index = pd.to_datetime(feed.index)
    cerebro = bt.Cerebro(stdstats=False)
    cerebro.adddata(bt.feeds.PandasData(dataname=feed))
    cerebro.addstrategy(ResearchClock)
    cerebro.run(runonce=False, preload=False)
    result = {"metrics": metrics(state["curve"], state["trades"], portfolio["starting_balance"]), "curve": state["curve"], "trades": state["trades"], "orders": state["orders"], "open_positions": list(state["positions"].values()), "costs": state["costs"], "events": events}
    if benchmark_payload is not None:
        from .domain import Instrument
        b_instrument = Instrument(symbol=instrument.benchmark, name="Market benchmark", market=instrument.market, benchmark=instrument.benchmark, role="benchmark")
        result["benchmark"] = simulate(benchmark_payload, b_instrument, fx_payload, params.model_dump(), costs, portfolio, start, end, buy_hold=True)
        result["buy_and_hold"] = simulate(payload, instrument, fx_payload, params.model_dump(), costs, portfolio, start, end, buy_hold=True)
        result["benchmark_relative_return"] = result["metrics"]["total_return"] - result["benchmark"]["metrics"]["total_return"]
    return result


def research_run(dataset, instrument, fx_dataset, benchmark, request, rules):
    params = request["parameters"]
    start, end = request["start"], request["end"]
    payload, fx = dataset.payload, fx_dataset.payload if fx_dataset else {"rates": []}
    days = [b["date"] for b in payload["bars"] if start <= b["date"] <= end]
    if len(days) < 250:
        raise ValueError("Research protocol requires ≥250 test-range sessions plus 200 warmup observations")
    warmup = sum(b["date"] < start for b in payload["bars"])
    if warmup < 200:
        raise ValueError("Start must leave at least 200 prior observations for indicator warmup")
    for source in [dataset, benchmark]:
        from .calendars import sessions
        available = {b["date"] for b in source.payload["bars"]}
        missing = set(sessions(instrument.market, start, end)) - available
        if missing:
            raise ValueError(f"{source.symbol}: {len(missing)} missing sessions; repair data or narrow the range. No forward-filled OHLCV")
    # Freeze the complete data, rules, source timestamps, code and assumptions.
    manifest = {"symbol": instrument.symbol, "instrument": instrument.model_dump(), "dataset_id": dataset.id, "dataset_hash": dataset.content_hash,
                "source_fetched_at": dataset.fetched_at, "source_provider": dataset.provider, "source_symbol": payload.get("source_symbol", dataset.symbol), "price_basis": payload.get("price_basis", "Raw provider OHLC"), "corporate_action_status": payload.get("actions_status"), "benchmark_dataset_id": benchmark.id, "benchmark_hash": benchmark.content_hash,
                "fx_dataset_id": fx_dataset.id if fx_dataset else None, "fx_hash": fx_dataset.content_hash if fx_dataset else None,
                "rules": rules.model_dump(), "request": request, "rule_version": RULE_VERSION, "code_sha256": code_version(),
                "assumptions": ASSUMPTIONS, "mode": dataset.mode, "engine": f"Backtrader {bt.__version__} event clock + MarketLens AUD execution ledger", "point_in_time_verified": False}
    def run(a, b, par=params, bench=False):
        return simulate(payload, instrument, fx, par, rules.costs.model_dump(), rules.portfolio.model_dump(), a, b, benchmark.payload if bench else None)
    full = run(start, end, bench=True)
    a, b = int(len(days) * .6), int(len(days) * .8)
    periods = {"train": [days[0], days[a - 1]], "validation": [days[a], days[b - 1]], "test": [days[b], days[-1]]}
    partitions = {name: {"period": dates, "metrics": run(*dates)["metrics"]} for name, dates in periods.items()}
    sensitivity, walk = [], []
    if request.get("robustness", True):
        candidates = sorted(set([max(.5, params["stop_atr"] * .75), params["stop_atr"], min(5, params["stop_atr"] * 1.25)]))
        for stop in candidates:
            par = {**params, "stop_atr": stop}
            result = run(*periods["train"], par=par)
            sensitivity.append({"parameters": par, "period": periods["train"], "metrics": result["metrics"]})
        # Expanding training window. Parameter selection strictly precedes each test block.
        for cut in (.55, .7, .85):
            boundary = int(len(days) * cut)
            finish = min(len(days) - 1, int(len(days) * (cut + .15)) - 1)
            choices = []
            for stop in candidates:
                par = {**params, "stop_atr": stop}
                trained = run(days[0], days[boundary - 1], par=par)["metrics"]
                choices.append((trained["total_return"], -stop, par))
            chosen = max(choices, key=lambda x: (x[0], x[1]))[2]
            tested = run(days[boundary], days[finish], par=chosen)
            walk.append({"train": [days[0], days[boundary - 1]], "test": [days[boundary], days[finish]], "selected_parameters": chosen, "metrics": tested["metrics"]})
    return {"manifest": manifest, "result_hash": digest(full), "results": full, "partitions": partitions, "sensitivity": sensitivity, "walk_forward": walk,
            "holdout_notice": "Chronological test is fixed for this saved run. The application cannot establish an untouched research history across repeated user experiments. Do not claim untouched OOS performance after inspecting or tuning against it."}
