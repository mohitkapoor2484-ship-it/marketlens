import importlib.util
import json
from pathlib import Path

from marketlens.config import ROOT


def test_pages_export_never_publishes_configured_credentials_or_database(tmp_path, monkeypatch):
    configured_database = tmp_path / "must-not-open.db"
    sentinel = "DO_NOT_PUBLISH_THIS_PROVIDER_KEY_TEST"
    monkeypatch.setenv("DATA_MODE", "eodhd")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{configured_database.as_posix()}")
    monkeypatch.setenv("EODHD_API_KEY", sentinel)
    monkeypatch.setenv("SCHEDULER_ENABLED", "true")
    def fail(*args, **kwargs):
        raise AssertionError("Pages exporter must never call a real provider")
    monkeypatch.setattr("marketlens.providers.EODHDProvider.fetch", fail)
    monkeypatch.setattr("marketlens.providers.EODHDProvider.fx", fail)
    spec = importlib.util.spec_from_file_location("marketlens_pages_export", ROOT / "scripts/export_pages.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    destination = tmp_path / "public/pages-data"
    result = module.export_preview(destination, robustness=False)
    assert result["genuine_forward_signals"] == 0
    assert result["stocks"] == 14
    assert not configured_database.exists()
    exported = [p.read_text(encoding="utf-8") for p in destination.rglob("*") if p.is_file()]
    assert all(sentinel not in text for text in exported)
    snapshot = json.loads((destination / "snapshot.json").read_text(encoding="utf-8"))
    assert snapshot["settings"]["environment"]["key_configured"] is False
    assert snapshot["settings"]["environment"]["scheduler_enabled"] is False
    assert snapshot["overview"]["paper"]["milestone"]["evaluable"] == 0
    assert (destination / f"paper/{snapshot['replay_account']}.json").exists()
    assert (destination / f"backtests/{snapshot['backtests'][0]['id']}.csv").exists()
