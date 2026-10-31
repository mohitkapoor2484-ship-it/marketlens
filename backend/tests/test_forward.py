"""Simulated time and provider responses here are test fixtures, never user evidence."""
import copy
from datetime import datetime, timezone

import pytest

from marketlens.config import ROOT, Settings
from marketlens.db import Database, Dataset, Evaluation, PaperAccount, Signal
from marketlens.domain import digest
from marketlens.execution import add_order
from marketlens.paper import account_view, evaluate
from marketlens.service import Service


@pytest.mark.parametrize("data_mode", ["eodhd", "yahoo"])
def test_forward_time_order_and_restart_idempotency(tmp_path, monkeypatch, data_mode):
    cfg = Settings(_env_file=None, data_mode=data_mode, database_url=f"sqlite:///{tmp_path.as_posix()}/forward.db", universe_path=ROOT / "universe.json", report_dir=tmp_path / "reports")
    svc = Service(cfg, Database(cfg.database_url))
    svc.initialise()
    signal = {"id": "test-fixture-signal", "symbol": "BHP.AU", "market": "ASX", "currency": "AUD", "strategy": "breakout", "asof": "2026-01-05", "recorded_at": "2026-01-06T00:00:00+00:00", "entry": 100, "stop": 90, "target": 130, "kind": "forward"}
    payload = {"market": "ASX", "currency": "AUD", "actions": [], "volume_basis": "test-fixture", "bars": [
        {"date": "2026-01-05", "open": 99, "high": 120, "low": 95, "close": 105, "volume": 100000},
        {"date": "2026-01-06", "open": 101, "high": 120, "low": 95, "close": 105, "volume": 100000},
        {"date": "2026-01-07", "open": 110, "high": 120, "low": 100, "close": 115, "volume": 100000}]}
    with svc.db.session() as s:
        s.add(Dataset(id="fixture-provider-snapshot", symbol="BHP.AU", mode=data_mode, provider="test-only-stub", fetched_at="2026-01-07T06:00:00+00:00", content_hash=digest(payload), payload=payload))
        s.add(Signal(id=signal["id"], dedupe=signal["id"], account_id="forward", symbol="BHP.AU", recorded_at=signal["recorded_at"], kind="forward", payload=signal, hash=digest(signal)))
        a = s.get(PaperAccount, "forward")
        state = copy.deepcopy(a.state)
        add_order(state, signal)
        a.state = state
    monkeypatch.setattr("marketlens.paper.now_utc", lambda: datetime(2026, 1, 8, tzinfo=timezone.utc))
    first = evaluate(svc)
    assert first["evaluated"] == 1  # Jan 6 opened before the recorded time and must be excluded.
    view = account_view(svc)
    assert view["state"]["positions"][signal["id"]]["entry_fill"]["raw_price"] == 110
    assert view["state"]["positions"][signal["id"]]["entry_date"] == "2026-01-07"
    assert view["milestone"]["evaluable"] == 1
    restored = Service(cfg, Database(cfg.database_url))
    restored.initialise()
    assert evaluate(restored)["evaluated"] == 0
    second = account_view(restored)
    assert view["state"] == second["state"]
    assert view["event_count"] == second["event_count"]
    assert second["audit_valid"]
