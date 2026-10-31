import copy
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from marketlens.calendars import session_times, sessions
from marketlens.domain import Bar, Costs, PortfolioRules, ScanRules, StrategyParams
from marketlens.execution import add_order, fill_cost, fx_rate, initial_state, process_bar
from marketlens.metrics import metrics
from marketlens.research import indicator_frame
from marketlens.strategies import detected, trade_plan


def fixture_payload(n=300):
    days = sessions("US", "2020-01-02", "2022-12-30")[:n]
    return {"bars": [{"date": d, "open": 100 + i * .1, "high": 101 + i * .1, "low": 99 + i * .1, "close": 100.5 + i * .1, "volume": 100000, "adjusted_close": 1} for i, d in enumerate(days)], "actions": [], "currency": "USD", "volume_basis": "synthetic"}


def test_indicators_do_not_change_when_future_bars_change():
    original = fixture_payload()
    modified = copy.deepcopy(original)
    for b in modified["bars"][250:]:
        for key in ("open", "high", "low", "close"):
            b[key] *= 10
        b["volume"] *= 20
    pd.testing.assert_frame_equal(indicator_frame(original).iloc[:250], indicator_frame(modified).iloc[:250])


def test_future_split_is_not_applied_to_earlier_indicators():
    data = fixture_payload()
    with_split = copy.deepcopy(data)
    day = data["bars"][250]["date"]
    with_split["actions"] = [{"kind": "split", "date": day, "ratio": 2}]
    for b in with_split["bars"][250:]:
        for k in ("open", "high", "low", "close"):
            b[k] /= 2
        b["volume"] *= 2
    actual, original = indicator_frame(with_split), indicator_frame(data)
    pd.testing.assert_frame_equal(actual.iloc[:250], original.iloc[:250])
    assert actual.iloc[250].sma200 == pytest.approx(original.iloc[250].sma200 / 2)
    assert actual.iloc[250].atr < 3


def test_known_indicator_values_and_adjusted_close_not_used():
    f = indicator_frame(fixture_payload())
    assert f.iloc[19].sma20 == pytest.approx(np.mean([100.5 + .1 * i for i in range(20)]))
    assert f.iloc[13].atr == pytest.approx(2)
    assert f.iloc[-1].rsi == 100
    assert f.iloc[198].sma200 != f.iloc[198].sma200
    assert f.iloc[-1].sma200 > 100
    assert f.iloc[-1].volume_ratio == 1


def test_asx_us_holidays_and_dst():
    assert "2025-01-09" not in sessions("US", "2025-01-08", "2025-01-10")
    assert "2026-01-26" not in sessions("ASX", "2026-01-23", "2026-01-28")
    assert "2026-07-03" not in sessions("US", "2026-07-01", "2026-07-06")
    from zoneinfo import ZoneInfo
    jan = session_times("US", "2026-01-05")[0].astimezone(ZoneInfo("Australia/Melbourne"))
    jul = session_times("US", "2026-07-06")[0].astimezone(ZoneInfo("Australia/Melbourne"))
    assert (jan.hour, jan.minute) == (1, 30)
    assert (jul.hour, jul.minute) == (23, 30)


def test_fx_is_prior_dated_and_fails_on_stale_or_missing():
    rates = [{"date": "2026-01-05", "rate": 1.5}, {"date": "2026-01-06", "rate": 999}]
    assert fx_rate(rates, "2026-01-06", "USD") == 1.5
    assert fx_rate([], "2026-01-06", "AUD") == 1
    with pytest.raises(ValueError):
        fx_rate(rates, "2026-01-05", "USD")
    with pytest.raises(ValueError):
        fx_rate(rates[:1], "2026-01-20", "USD")


def test_costs_independently_calculated():
    c = Costs(commission_bps=10, minimum_fee_aud=3, spread_bps=20, slippage_bps=10, fx_fee_bps=30)
    buy = fill_cost(100, 10, 1.5, "USD", "buy", c)
    assert buy["price"] == pytest.approx(100.2)
    assert buy["notional"] == pytest.approx(1503)
    assert buy["commission"] == 3
    assert buy["fx"] == pytest.approx(4.509)
    assert buy["cash"] == pytest.approx(1510.509)
    sell = fill_cost(100, 10, 1.5, "USD", "sell", c)
    assert sell["cash"] == pytest.approx(1497 - 3 - 4.491)
    assert fill_cost(100, 10, 1, "AUD", "buy", c)["fx"] == 0


def zero_costs(**kwargs):
    return Costs(commission_bps=0, minimum_fee_aud=0, spread_bps=0, slippage_bps=0, fx_fee_bps=0, **kwargs)


def signal():
    return {"id": "s1", "symbol": "BHP.AU", "asof": "2026-01-05", "recorded_at": "2026-01-05T06:00:00+00:00", "strategy": "breakout", "market": "ASX", "currency": "AUD", "entry": 100, "stop": 90, "target": 120}


def bar(**kwargs):
    return {"date": "2026-01-06", "open": 101, "high": 110, "low": 95, "close": 105, "volume": 100000, **kwargs}


def test_no_same_signal_bar_fill():
    st = initial_state()
    add_order(st, signal())
    assert process_bar(st, "BHP.AU", bar(date="2026-01-05"), 1, zero_costs(), PortfolioRules()) == []
    assert not st["positions"]


def test_gap_entry_and_gap_stop_use_adverse_open():
    st = initial_state()
    add_order(st, signal())
    process_bar(st, "BHP.AU", bar(open=105, low=100), 1, zero_costs(), PortfolioRules())
    assert st["positions"]["s1"]["entry_price"] == 105
    process_bar(st, "BHP.AU", bar(date="2026-01-07", open=80, high=85, low=75, close=82), 1, zero_costs(), PortfolioRules())
    assert st["trades"][0]["exit_price"] == 80
    assert st["trades"][0]["reason"] == "stopped"


def test_stop_wins_ambiguous_bar_and_no_entry_bar_target():
    st = initial_state()
    add_order(st, signal())
    process_bar(st, "BHP.AU", bar(high=130, low=95), 1, zero_costs(), PortfolioRules())
    assert not st["trades"]  # High touched target, but entry-bar target is not credited.
    process_bar(st, "BHP.AU", bar(date="2026-01-07", high=130, low=80), 1, zero_costs(), PortfolioRules())
    assert st["trades"][0]["reason"] == "stopped"
    assert st["trades"][0]["exit_price"] == 90


def test_entry_bar_stop_is_conservative():
    st = initial_state()
    add_order(st, signal())
    process_bar(st, "BHP.AU", bar(high=130, low=80), 1, zero_costs(), PortfolioRules())
    assert st["trades"][0]["reason"] == "stopped"


def test_volume_partial_cash_and_expiry():
    st = initial_state()
    add_order(st, signal())
    process_bar(st, "BHP.AU", bar(volume=200), 1, zero_costs(), PortfolioRules())
    assert st["orders"][0]["status"] == "partially_filled"
    assert st["positions"]["s1"]["quantity"] == 2
    assert st["cash"] >= 0
    expired = initial_state()
    add_order(expired, signal())
    for day in ("2026-01-06", "2026-01-07", "2026-01-08"):
        process_bar(expired, "BHP.AU", bar(date=day, open=80, high=85, low=75, close=82), 1, zero_costs(), PortfolioRules())
    assert expired["orders"][0]["status"] == "expired"


def test_split_and_dividend_accounting():
    st = initial_state()
    add_order(st, signal())
    process_bar(st, "BHP.AU", bar(), 1, zero_costs(), PortfolioRules())
    before = copy.deepcopy(st["positions"]["s1"])
    actions = [{"kind": "split", "date": "2026-01-07", "ratio": 2}, {"kind": "dividend", "date": "2026-01-07", "payment_date": "2026-01-08", "amount": 1, "currency": "AUD"}]
    process_bar(st, "BHP.AU", bar(date="2026-01-07", open=51, high=54, low=48, close=52), 1, zero_costs(), PortfolioRules(), actions)
    pos = st["positions"]["s1"]
    assert pos["quantity"] == before["quantity"] * 2
    assert pos["stop"] == 45
    assert st["receivables"][0]["amount_aud"] == pos["quantity"]
    old_cash = st["cash"]
    process_bar(st, "BHP.AU", bar(date="2026-01-08", open=51, high=54, low=48, close=52), 1, zero_costs(), PortfolioRules(), actions)
    assert st["cash"] == old_cash + pos["quantity"]
    assert not st["receivables"]


def test_metrics_have_independent_known_outcomes():
    curve = [{"date": "2020-01-01", "equity": 10000, "exposure": .5}, {"date": "2021-01-01", "equity": 12000, "exposure": .5}, {"date": "2022-01-01", "equity": 9000, "exposure": .5}]
    trades = [{"pnl_aud": 200, "r_multiple": 2}, {"pnl_aud": -100, "r_multiple": -1}]
    m = metrics(curve, trades, 10000)
    assert m["total_return"] == pytest.approx(-.1)
    assert m["max_drawdown"] == pytest.approx(-.25)
    assert m["profit_factor"] == 2
    assert m["expectancy_aud"] == 50
    assert m["average_r"] == .5
    assert m["win_rate"] == .5
    assert m["sharpe"] is None
    assert metrics([], [], 10000)["win_rate"] is None


def test_invalid_ohlcv_and_score_weights_rejected():
    with pytest.raises(ValueError):
        Bar(date="2026-01-01", open=100, high=80, low=90, close=99, volume=1)
    with pytest.raises(ValueError):
        ScanRules(weights={"trend": 100})
    with pytest.raises(ValueError):
        Costs(slippage_bps=float("nan"))
