import copy
from datetime import datetime, timezone

import pytest

from marketlens.db import Job, PaperAccount, RuleRevision, append_event
from marketlens.domain import AppRules, Costs, PortfolioRules, stamp
from marketlens.execution import add_order, initial_state, mark_curve, process_bar
from marketlens.jobs import tick
from marketlens.paper import account_view


def test_forward_risk_and_cost_assumptions_cannot_be_edited(service):
    from sqlalchemy import text
    with pytest.raises(Exception, match="immutable account assumptions"):
        with service.db.engine.begin() as conn:
            conn.execute(text("UPDATE paper_accounts SET config='{}' WHERE id='forward'"))


def test_audit_export_is_complete_beyond_ui_limit(service):
    with service.db.session() as s:
        for n in range(255):
            append_event(s, "export-test-cohort", f"export-test:{n}", {"type": "test_event", "n": n})
        original = s.get(PaperAccount, "forward")
        s.add(PaperAccount(id="export-test-cohort", kind="replay", config=original.config, created_at=stamp(), state=initial_state()))
    ui, full = account_view(service, "export-test-cohort"), account_view(service, "export-test-cohort", full=True)
    assert len(ui["events"]) == 250
    assert len(full["events"]) == full["event_count"] == 255
    assert full["audit_valid"]
    assert full["events"][0]["previous_hash"] == "0" * 64


def test_bad_fx_and_corporate_events_are_rejected(service):
    with pytest.raises(ValueError, match="FX"):
        service.save_dataset("USDAUD.FOREX", {"rates": [{"date": "2025-01-01", "rate": -2}]})
    from marketlens.db import latest_dataset
    with service.db.session() as s:
        payload = copy.deepcopy(latest_dataset(s, "BHP.AU", "demo").payload)
    payload["actions"] = [{"kind": "split", "date": "2025-01-02", "ratio": 0}]
    with pytest.raises(ValueError, match="split ratio"):
        service.save_dataset("BHP.AU", payload)


def test_initial_drawdown_includes_entry_friction():
    state = initial_state(10000)
    state["cash"] = 9900
    mark_curve(state, "2026-01-05")
    assert state["curve"][0]["drawdown"] == pytest.approx(-.01)


def test_close_scheduler_dedupes_completed_market_sessions(service, monkeypatch):
    monkeypatch.setattr(service.settings, "data_mode", "eodhd")
    calls = []
    monkeypatch.setattr("marketlens.jobs.cycle_checked", lambda svc, market: calls.append(market) or {"market": market})
    monkeypatch.setattr("marketlens.jobs.now_utc", lambda: datetime(2026, 10, 30, 22, tzinfo=timezone.utc))
    try:
        tick(service)
        tick(service)
        assert calls == ["ASX", "US"]
    finally:
        monkeypatch.setattr(service.settings, "data_mode", "demo")
