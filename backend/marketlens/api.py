import csv
import io
import json
import logging
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import Field, model_validator

from .config import ROOT, settings
from .db import Backtest, Database, RuleRevision, Watch, rules
from .domain import AppRules, Instrument, StrategyParams, StrictModel, stamp
from .jobs import cycle, start_scheduler
from .paper import account_view, cancel, evaluate, replay
from .service import Service
from .strategies import STRATEGIES

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")


class BacktestRequest(StrictModel):
    symbol: str = "BHP.AU"
    start: date = date(2020, 1, 2)
    end: date = date(2025, 12, 31)
    parameters: StrategyParams = Field(default_factory=StrategyParams)
    robustness: bool = True
    dataset_id: str | None = None
    rerun_id: str | None = None

    @model_validator(mode="after")
    def ordered(self):
        if self.end <= self.start:
            raise ValueError("End must follow start")
        return self


class SymbolRequest(StrictModel):
    symbol: str


class UniverseRequest(StrictModel):
    items: list[Instrument] = Field(min_length=3, max_length=500)
    expected_revision: int = Field(ge=1)


def create_app(service=None):
    svc = service or Service(settings, Database(settings.database_url))
    mutation_lock = threading.Lock()

    @asynccontextmanager
    async def lifespan(app):
        svc.initialise()
        scheduler = start_scheduler(svc) if svc.settings.scheduler_enabled or svc.settings.publish_enabled else None
        app.state.service = svc
        yield
        if scheduler:
            scheduler.shutdown(wait=False)

    app = FastAPI(title="MarketLens Research API", version="1.0.0", lifespan=lifespan,
                  description="Local EOD research and simulation only. No live trading routes exist.")
    app.add_middleware(CORSMiddleware, allow_origins=svc.settings.cors_origins.split(","), allow_credentials=False, allow_methods=["GET", "POST", "PUT", "DELETE"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def local_security(request: Request, call_next):
        host = request.headers.get("host", "").split(":")[0]
        origin = request.headers.get("origin")
        allowed = set(svc.settings.cors_origins.split(",")) | {"http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:8001", "http://127.0.0.1:8001"}
        if host not in {"localhost", "127.0.0.1", "testserver"}:
            return JSONResponse({"detail": "Local-only service: untrusted Host"}, status_code=403)
        if request.method not in {"GET", "HEAD", "OPTIONS"} and origin and origin not in allowed:
            return JSONResponse({"detail": "Untrusted request origin"}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Cache-Control"] = "no-store"
        response.headers["Content-Security-Policy"] = "default-src 'self'; connect-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'"
        if request.url.path in {"/docs", "/redoc"}:
            response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; img-src 'self' data: https://fastapi.tiangolo.com; font-src 'self'; frame-ancestors 'none'"
        return response

    @app.exception_handler(ValueError)
    async def value_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        logging.getLogger("marketlens.api").error("Unhandled %s", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Operation failed. Inspect Data Health and local logs; no synthetic fallback was used."})

    def mutate(function):
        if not mutation_lock.acquire(blocking=False):
            raise HTTPException(409, "A research operation is running. Wait for completion before retrying.")
        try:
            with svc.operations:
                return function()
        finally:
            mutation_lock.release()

    @app.get("/api/status")
    def status():
        return {"app": "MarketLens", "version": "1.0.0", "mode": svc.settings.data_mode, "timezone": "Australia/Melbourne", "currency": "AUD", "trading": "research and paper simulation only", "timestamp": stamp()}

    @app.get("/api/overview")
    def overview():
        from .publication import publication_status
        scan = svc.latest_scan()
        return {"scan": scan, "paper": account_view(svc), "health": svc.data_health(), "publication": publication_status(svc)}

    @app.get("/api/scanner")
    def scanner():
        return svc.latest_scan()

    @app.post("/api/scans")
    def scan():
        return mutate(svc.scan)

    @app.post("/api/data/refresh")
    def refresh():
        return mutate(svc.refresh)

    @app.post("/api/jobs/cycle")
    def run_cycle():
        return mutate(lambda: cycle(svc))

    @app.get("/api/publication")
    def publication():
        from .publication import publication_status
        return publication_status(svc)

    @app.post("/api/publication/upload")
    def upload():
        from .publication import upload_tick
        return mutate(lambda: upload_tick(svc))

    @app.post("/api/publication/refresh")
    def refresh_and_upload():
        # The local-only API can perform source requests. The public site cannot.
        def run():
            if svc.settings.data_mode == "yahoo":
                svc.provider.news.cache.clear()
            return cycle(svc)
        return mutate(run)

    @app.get("/api/data/health")
    def health():
        return svc.data_health()

    @app.get("/api/stocks/{symbol}")
    def stock(symbol: str):
        return svc.stock(symbol)

    @app.get("/api/news")
    def news():
        scan = svc.latest_scan()
        articles = []
        for c in scan["candidates"]:
            articles.extend({**n, "symbol": c["symbol"]} for n in c["news"])
        return {"articles": sorted(articles, key=lambda n: n["published_at"], reverse=True), "sources": [{"symbol": c["symbol"], "status": c["news_status"]} for c in scan["candidates"]], "mode": svc.settings.data_mode}

    @app.get("/api/settings")
    def get_settings():
        with svc.db.session() as s:
            config = rules(s).model_dump()
        return {"rules": config, "universe": [i.model_dump() for i in svc.universe], "strategies": STRATEGIES, "provider": svc.data_health()["provider"], "environment": {"mode": svc.settings.data_mode, "key_configured": bool(svc.settings.eodhd_api_key.get_secret_value()) if svc.settings.data_mode == "eodhd" else "not required", "key_required": svc.settings.data_mode == "eodhd", "news_provider": "Google News RSS" if svc.settings.data_mode == "yahoo" else svc.settings.data_mode, "request_spacing_seconds": svc.settings.yahoo_request_interval_seconds if svc.settings.data_mode == "yahoo" else None, "news_enabled": svc.settings.enable_news, "fundamentals_enabled": svc.settings.enable_fundamentals, "scheduler_enabled": svc.settings.scheduler_enabled, "daily_credit_budget": svc.settings.eodhd_daily_budget if svc.settings.data_mode == "eodhd" else "not applicable", "history_years": svc.settings.history_years, "timezone": "Australia/Melbourne"}, "notice": "Yahoo Finance and Google News RSS require no API keys. Provider selection and schedules are configured in .env. A rule edit creates a new research revision; the existing forward account keeps its frozen strategy/cost/risk assumptions."}

    @app.put("/api/settings")
    def update_settings(body: AppRules):
        with svc.db.session() as s:
            s.add(RuleRevision(created_at=stamp(), payload=body.model_dump()))
        return {"saved": True, "rules": body.model_dump()}

    @app.get("/api/universe")
    def get_universe():
        from .universe import universe_view
        with svc.operations:
            return universe_view(svc)

    @app.put("/api/universe")
    def update_universe(body: UniverseRequest):
        from .universe import UniverseConflict, save_universe
        try:
            return mutate(lambda: save_universe(svc, [i.model_dump() for i in body.items], body.expected_revision))
        except UniverseConflict as exc:
            raise HTTPException(409, str(exc)) from None

    @app.get("/api/watchlist")
    def watchlist():
        with svc.db.session() as s:
            return [{"symbol": w.symbol, "added_at": w.added_at} for w in s.query(Watch).order_by(Watch.added_at)]

    @app.post("/api/watchlist")
    def add_watch(body: SymbolRequest):
        svc.instrument(body.symbol)
        with svc.db.session() as s:
            if not s.get(Watch, body.symbol):
                s.add(Watch(symbol=body.symbol, added_at=stamp()))
        return {"saved": True}

    @app.delete("/api/watchlist/{symbol}")
    def remove_watch(symbol: str):
        with svc.db.session() as s:
            row = s.get(Watch, symbol)
            if row:
                s.delete(row)
        return {"removed": True}

    @app.get("/api/backtests")
    def history():
        with svc.db.session() as s:
            return [{"id": b.id, "created_at": b.created_at, "symbol": b.payload["manifest"]["symbol"], "mode": b.payload["manifest"]["mode"], "request": b.payload["manifest"]["request"], "metrics": b.payload["results"]["metrics"], "result_hash": b.payload["result_hash"]} for b in s.query(Backtest).order_by(Backtest.created_at.desc()).limit(50)]

    @app.post("/api/backtests")
    def run_backtest(body: BacktestRequest):
        return mutate(lambda: svc.job("backtest", lambda: svc.backtest(body.model_dump(mode="json"))))

    @app.get("/api/backtests/{identifier}")
    def get_backtest(identifier: str):
        with svc.db.session() as s:
            row = s.get(Backtest, identifier)
            if not row:
                raise HTTPException(404, "Backtest not found")
            return {"id": row.id, **row.payload}

    @app.get("/api/backtests/{identifier}/export")
    def export(identifier: str, format: Literal["json", "csv"] = "json"):
        data = get_backtest(identifier)
        if format == "json":
            return Response(json.dumps(data, indent=2, allow_nan=False), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="backtest-{identifier}.json"'})
        stream = io.StringIO()
        fields = ["symbol", "strategy", "entry_date", "exit_date", "quantity", "entry_price", "exit_price", "entry_fx", "exit_fx", "pnl_aud", "r_multiple", "reason"]
        writer = csv.DictWriter(stream, fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data["results"]["trades"])
        return Response(stream.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="trades-{identifier}.csv"'})

    @app.get("/api/paper")
    def paper(account: str = "forward"):
        return account_view(svc, account)

    @app.post("/api/paper/evaluate")
    def evaluate_paper():
        return mutate(lambda: svc.job("paper:evaluate", lambda: evaluate(svc)))

    @app.post("/api/paper/replay")
    def replay_paper(body: SymbolRequest):
        return mutate(lambda: svc.job("paper:fixture-replay", lambda: replay(svc, body.symbol)))

    @app.post("/api/paper/orders/{signal_id}/cancel")
    def cancel_paper(signal_id: str):
        return mutate(lambda: cancel(svc, signal_id))

    @app.get("/api/paper/export")
    def export_paper(account: str = "forward"):
        return Response(json.dumps(account_view(svc, account, full=True), indent=2), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="paper-{account}.json"'})

    @app.post("/api/reports")
    def reports(weekly: bool = False):
        return mutate(lambda: svc.job("report:weekly" if weekly else "report:daily", lambda: svc.report(weekly)))

    @app.get("/api/reports")
    def report_list():
        return [{"name": p.name, "bytes": p.stat().st_size, "url": f"/api/reports/{p.name}"} for p in sorted(svc.settings.report_dir.glob("*.md"), reverse=True)]

    @app.get("/api/reports/{name}")
    def download_report(name: str):
        if Path(name).name != name or not name.endswith((".md", ".json")):
            raise HTTPException(400, "Invalid report filename")
        path = svc.settings.report_dir / name
        if not path.is_file():
            raise HTTPException(404, "Report not found")
        return FileResponse(path, filename=name)

    dist = ROOT / "frontend/dist"
    if dist.is_dir():
        app.mount("/", StaticFiles(directory=dist, html=True), name="dashboard")
    return app


app = create_app()
