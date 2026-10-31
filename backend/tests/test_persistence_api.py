import copy
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from marketlens.api import create_app
from marketlens.db import Backtest, Dataset, PaperAccount, RuleRevision, Signal, append_event, latest_dataset
from marketlens.domain import canonical, digest, stamp
from marketlens.paper import account_view, evaluate, record_signals, replay
from marketlens.providers import EODHDProvider, ProviderError
from marketlens.research import indicator_frame


def test_scan_deterministic_and_score_sum(service):
    a, b = service.scan(record_forward=False), service.scan(record_forward=False)
    assert a["candidates"] == b["candidates"]
    assert len(a["shortlist"]) <= 10
    for c in a["candidates"]:
        assert c["score"] == pytest.approx(sum(c["score_components"].values()))
        assert c["mode"] == "demo"
        assert not c["news"]
    assert account_view(service)["milestone"]["generated"] == 0


@pytest.mark.parametrize("table", ["datasets", "scans", "rule_revisions"])
def test_database_enforces_append_only(service, table):
    with pytest.raises(Exception, match="immutable audit record"):
        with service.db.engine.begin() as conn:
            conn.execute(text(f"DELETE FROM {table}"))


def test_dataset_missing_session_rejected_by_backtest(service):
    with service.db.session() as s:
        original = latest_dataset(s, "BHP.AU", "demo")
        data = copy.deepcopy(original.payload)
    data["bars"] = [b for b in data["bars"] if b["date"] != "2023-01-03"]
    identifier = service.save_dataset("BHP.AU", data)
    try:
        with pytest.raises(ValueError, match="missing sessions"):
            service.backtest({"symbol": "BHP.AU", "dataset_id": identifier, "start": "2022-01-04", "end": "2025-12-31", "parameters": {"strategy": "breakout", "lookback": 20, "stop_atr": 2, "target_r": 2}, "robustness": False})
    finally:
        service.save_dataset("BHP.AU", original.payload)


def test_frozen_backtest_rerun_ignores_later_parameter_edits(service):
    req = {"symbol": "MSFT.US", "start": "2020-01-02", "end": "2022-12-30", "parameters": {"strategy": "momentum_continuation", "lookback": 20, "stop_atr": 2, "target_r": 2}, "robustness": False}
    first = service.backtest(req)
    with service.db.session() as s:
        old = s.query(RuleRevision).order_by(RuleRevision.id.desc()).first().payload
        altered = copy.deepcopy(old)
        altered["costs"]["minimum_fee_aud"] = 50
        s.add(RuleRevision(created_at=stamp(), payload=altered))
    second = service.backtest({**req, "rerun_id": first["id"]})
    assert second["rerun_matches"] is True
    assert first["result_hash"] == second["result_hash"]
    assert first["results"]["trades"] == second["results"]["trades"]
    with service.db.session() as s:
        s.add(RuleRevision(created_at=stamp(), payload=old))


def test_replay_is_isolated_immutable_and_audited(service):
    before = account_view(service)
    demo = replay(service, "NVDA.US")
    after = account_view(service)
    assert demo["kind"] == "replay"
    assert demo["audit_valid"]
    assert demo["milestone"]["generated"] == 0
    assert before["state"] == after["state"]
    assert after["milestone"]["generated"] == 0
    with pytest.raises(Exception, match="immutable audit record"):
        with service.db.engine.begin() as conn:
            conn.execute(text("UPDATE signals SET recorded_at='1900-01-01'"))
    with pytest.raises(Exception, match="immutable audit record"):
        with service.db.engine.begin() as conn:
            conn.execute(text("DELETE FROM paper_events"))


def test_forward_engine_cannot_consume_fixtures(service):
    assert evaluate(service)["evaluated"] == 0
    assert account_view(service)["milestone"]["evaluable"] == 0


def test_forward_registration_rejects_demo_even_if_called_directly(service):
    record_signals(service, service.latest_scan()["shortlist"])
    assert account_view(service)["milestone"]["generated"] == 0


def test_provider_missing_credentials_no_synthetic_fallback(service):
    config = service.settings.model_copy(update={"eodhd_daily_budget": 20})
    provider = EODHDProvider(config, service.db)
    with pytest.raises(ProviderError, match="personal EODHD key"):
        provider.get("eod/BHP.AU")


def test_provider_failure_is_visible_and_budgeted(service, monkeypatch):
    from pydantic import SecretStr
    import httpx
    config = service.settings.model_copy(update={"eodhd_api_key": SecretStr("test-secret"), "eodhd_requests_per_minute": 1000})
    provider = EODHDProvider(config, service.db)
    monkeypatch.setattr("marketlens.providers.httpx.get", lambda *a, **k: httpx.Response(403))
    with pytest.raises(ProviderError, match="HTTP 403") as exc:
        provider.get("eod/BHP.AU")
    assert "test-secret" not in str(exc.value)


def test_job_failure_persists(service):
    def fail():
        raise ValueError("Fixture provider unavailable")
    with pytest.raises(ValueError):
        service.job("test-failure", fail)
    assert any(j["key"] == "test-failure" and j["status"] == "failed" for j in service.data_health()["jobs"])


def test_api_major_journeys_and_no_broker_surface(service):
    with TestClient(create_app(service)) as client:
        for path in ("/api/status", "/api/overview", "/api/scanner", "/api/settings", "/api/news", "/api/data/health", "/api/paper", "/api/backtests"):
            response = client.get(path)
            assert response.status_code == 200, (path, response.text)
        stock = client.get("/api/stocks/BHP.AU").json()
        assert len(stock["bars"]) == 600
        assert client.post("/api/watchlist", json={"symbol": "AAPL.US"}).status_code == 200
        assert any(w["symbol"] == "AAPL.US" for w in client.get("/api/watchlist").json())
        assert client.delete("/api/watchlist/AAPL.US").status_code == 200
        assert client.post("/api/reports").status_code == 200
        assert client.get("/api/reports").json()
        assert client.get("/api/paper/export").status_code == 200
        assert client.post("/api/jobs/cycle", headers={"Origin": "https://hostile.example"}).status_code == 403
        assert client.get("/api/status", headers={"Host": "hostile.example"}).status_code == 403
        schema = client.get("/openapi.json").json()
        assert not any(x in path.lower() for path in schema["paths"] for x in ("broker", "live-trade", "submit-order"))
        assert "test-secret" not in client.get("/api/settings").text


def test_restart_preserves_signal_and_account_state(service):
    from marketlens.db import Database
    from marketlens.service import Service
    before = account_view(service)
    restored = Service(service.settings, Database(service.settings.database_url))
    restored.initialise()
    after = account_view(restored)
    assert before["state"] == after["state"]
    assert before["milestone"] == after["milestone"]
    assert after["audit_valid"]


def test_append_event_dedupe(service):
    with service.db.session() as s:
        append_event(s, "forward", "idempotency-test", {"type": "test"})
        append_event(s, "forward", "idempotency-test", {"type": "test"})
    view = account_view(service)
    assert sum(e["type"] == "test" for e in view["events"]) == 1
    assert view["audit_valid"]
