"""Build an explicitly synthetic, read-only Pages preview in an isolated database.

Never uses the configured local database or provider keys, or fetches provider data.
"""
import csv
import io
import json
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "backend"))

from marketlens.backtests import code_version
from marketlens.config import Settings
from marketlens.db import Backtest, Database, rules
from marketlens.paper import account_view, replay
from marketlens.service import Service
from marketlens.strategies import STRATEGIES


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, separators=(",", ":"), allow_nan=False), encoding="utf-8")


def export_preview(output=None, robustness=True):
    output = Path(output or PROJECT / "frontend/public/pages-data")
    with TemporaryDirectory(prefix="marketlens-pages-") as temporary:
        config = Settings(
            _env_file=None, data_mode="demo", database_url="sqlite:///:memory:",
            eodhd_api_key="", enable_news=False, enable_fundamentals=False,
            scheduler_enabled=False, initial_rules_path=None,
            universe_path=PROJECT / "universe.json", report_dir=Path(temporary) / "reports",
        )
        service = Service(config, Database(config.database_url))
        service.initialise()
        example = service.backtest({
            "symbol": "AAPL.US", "start": "2020-01-02", "end": "2025-12-31",
            "parameters": {"strategy": "momentum_continuation", "lookback": 20, "stop_atr": 2, "target_r": 2},
            "robustness": robustness,
        })
        replay_account = replay(service, "AAPL.US")
        forward = account_view(service, full=True)
        assert forward["milestone"]["generated"] == forward["milestone"]["evaluable"] == 0
        assert replay_account["kind"] == "replay" and replay_account["audit_valid"]
        scan, health = service.latest_scan(), service.data_health()
        assert scan["mode"] == health["mode"] == "demo"
        health["hosting"] = "GitHub Pages static fixture preview; no running Python engine"
        health["jobs"] = []  # Build-time fixture jobs are not hosted/scheduled jobs.
        with service.db.session() as session:
            settings = {
                "rules": rules(session).model_dump(), "universe": [i.model_dump() for i in service.universe],
                "strategies": STRATEGIES, "provider": health["provider"],
                "environment": {"mode": "demo", "key_configured": False, "news_enabled": False,
                                "fundamentals_enabled": False, "scheduler_enabled": False,
                                "daily_credit_budget": 0, "history_years": 8, "timezone": "Australia/Melbourne",
                                "hosting": "Static Pages preview — research engine offline"},
                "notice": "This is a read-only synthetic Pages preview. Run the Python service locally to configure rules, use provider keys, generate new backtests or collect forward signals. Browser watchlists are saved only on this device.",
            }
            saved = session.get(Backtest, example["id"])
            history = [{"id": saved.id, "created_at": saved.created_at, "symbol": "AAPL.US", "mode": "demo",
                        "request": example["manifest"]["request"], "metrics": example["results"]["metrics"], "result_hash": example["result_hash"]}]
        reports = []
        for weekly in (False, True):
            names = service.report(weekly=weekly)
            for name in names.values():
                target = output / "reports" / name
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((config.report_dir / name).read_bytes())
            markdown = names["markdown"]
            reports.append({"name": markdown, "bytes": (config.report_dir / markdown).stat().st_size,
                            "url": f"reports/{markdown}"})
        snapshot = {
            "preview": {"kind": "synthetic_static_fixture", "code_sha256": code_version(), "forward_signals": 0,
                        "notice": "No market observations, provider keys, user databases or actual trading outcomes are published."},
            "overview": {"scan": scan, "paper": forward, "health": health},
            "settings": settings, "backtests": history, "reports": reports,
            "replay_account": replay_account["id"],
        }
        write_json(output / "snapshot.json", snapshot)
        write_json(output / "paper/forward.json", forward)
        write_json(output / f"paper/{replay_account['id']}.json", account_view(service, replay_account["id"], full=True))
        write_json(output / f"backtests/{example['id']}.json", example)
        stream = io.StringIO()
        writer = csv.DictWriter(stream, ["symbol", "strategy", "entry_date", "exit_date", "quantity", "entry_price", "exit_price", "entry_fx", "exit_fx", "pnl_aud", "r_multiple", "reason"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(example["results"]["trades"])
        (output / f"backtests/{example['id']}.csv").write_text(stream.getvalue(), encoding="utf-8")
        for instrument in service.universe:
            stock = service.stock(instrument.symbol)
            assert stock["mode"] == "demo" and not stock["news"]
            write_json(output / f"stocks/{instrument.symbol}.json", stock)
        output.parent.mkdir(parents=True, exist_ok=True)
        (output.parent / ".nojekyll").write_text("", encoding="utf-8")
        service.db.engine.dispose()
        return {"output": str(output), "stocks": len(service.universe), "backtests": len(history),
                "fixture_replay_signals": len(replay_account["state"]["orders"]), "genuine_forward_signals": 0}


if __name__ == "__main__":
    print(json.dumps(export_preview(), indent=2))
