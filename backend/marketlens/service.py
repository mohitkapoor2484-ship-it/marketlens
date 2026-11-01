import json
import logging
import math
import uuid
from datetime import date, timedelta
from pathlib import Path
from threading import RLock

from .calendars import latest_closed, session_times, sessions
from .db import Backtest, Dataset, Job, PaperAccount, RuleRevision, Scan, Signal, latest_dataset, rules
from .domain import Bar, Instrument, RULE_VERSION, canonical, digest, now_utc, parse_time, stamp
from .execution import initial_state
from .providers import DemoProvider, EODHDProvider, PROVIDER_INFO
from .public_data import YahooProvider
from .research import finite_row, indicator_frame
from .strategies import rank_candidate
from .universe import load_universe, validate_universe

log = logging.getLogger("marketlens")


class Service:
    def __init__(self, settings, db):
        self.settings, self.db = settings, db
        self.operations = RLock()
        self.provider = {"demo": DemoProvider, "eodhd": lambda: EODHDProvider(settings, db), "yahoo": lambda: YahooProvider(settings, db)}[settings.data_mode]()
        self.universe = validate_universe(json.loads(settings.universe_path.read_text(encoding="utf-8")))
        self.universe_revision = 0
        from .discovery import YahooDiscovery
        self.discovery_provider = YahooDiscovery(settings)
        from .moomoo import MoomooBridge
        self.moomoo_provider = MoomooBridge(settings)

    def instrument(self, symbol):
        found = next((x for x in self.universe if x.symbol == symbol), None)
        if not found:
            raise ValueError("Symbol is not in the current universe; add it in Settings → Market universe")
        return found

    def initialise(self):
        from .domain import AppRules
        initial = AppRules.model_validate(json.loads(self.settings.initial_rules_path.read_text(encoding="utf-8"))) if self.settings.initial_rules_path else None
        self.db.migrate(initial)
        load_universe(self)
        from .moomoo import initialise_intraday
        initialise_intraday(self)
        with self.db.session() as s:
            if not s.get(PaperAccount, "forward"):
                config = rules(s).model_dump()
                s.add(PaperAccount(id="forward", kind="forward", created_at=stamp(), config=config, state=initial_state(config["portfolio"]["starting_balance"])))
            empty = latest_dataset(s, self.universe[0].symbol, self.settings.data_mode) is None
        if empty and self.settings.data_mode == "demo":
            self.refresh()
            self.scan()
        elif self.latest_scan().get("created_at") and self.latest_scan().get("universe_revision") != self.universe_revision:
            self.scan(record_forward=False)

    def save_dataset(self, symbol, payload, fetched_at=None):
        fetched_at = fetched_at or stamp()
        fetched_time = parse_time(fetched_at)
        if fetched_time > now_utc() + timedelta(seconds=5):
            raise ValueError("Retrieval timestamp is in the future")
        if "bars" in payload:
            days = [b["date"] for b in payload["bars"]]
            if days != sorted(set(days)):
                raise ValueError("Provider dates must be unique and increasing")
            expected = set(sessions(payload["market"], days[0], days[-1]))
            if set(days) - expected:
                raise ValueError("Provider supplied a bar outside an exchange session")
            for bar in payload["bars"]:
                Bar.model_validate(bar)
                if session_times(payload["market"], bar["date"])[1] > parse_time(fetched_at):
                    raise ValueError("Incomplete or future market bar rejected")
            missing = sorted(expected - set(days))
            for action in payload.get("actions", []):
                date.fromisoformat(action["date"])
                if action["kind"] == "split":
                    if not math.isfinite(action["ratio"]) or action["ratio"] <= 0:
                        raise ValueError("Invalid corporate split ratio")
                elif action["kind"] == "dividend":
                    if not math.isfinite(action["amount"]) or action["amount"] < 0:
                        raise ValueError("Invalid dividend cash amount")
                    if action.get("payment_date"):
                        date.fromisoformat(action["payment_date"])
                else:
                    raise ValueError("Unsupported corporate action type")
            payload = {**payload, "missing_sessions": missing, "source_timestamp_basis": "Exchange session close; vendor publication timestamp unavailable; retrieval recorded separately"}
        if "rates" in payload:
            days = [r["date"] for r in payload["rates"]]
            if not days or days != sorted(set(days)):
                raise ValueError("FX dates must be nonempty, unique and increasing")
            for rate in payload["rates"]:
                if date.fromisoformat(rate["date"]) > fetched_time.date() or not math.isfinite(rate["rate"]) or rate["rate"] <= 0:
                    raise ValueError("Invalid or future FX observation")
        content_hash = digest(payload)
        identifier = digest({"symbol": symbol, "mode": self.settings.data_mode, "fetched_at": fetched_at, "content": content_hash})
        with self.db.session() as s:
            existing = latest_dataset(s, symbol, self.settings.data_mode)
            if existing and existing.content_hash == content_hash and self.settings.data_mode == "demo":
                return existing.id
            if not s.get(Dataset, identifier):
                s.add(Dataset(id=identifier, symbol=symbol, mode=self.settings.data_mode, provider=self.settings.data_mode, fetched_at=fetched_at, content_hash=content_hash, payload=payload))
        return identifier

    def job(self, key, function):
        with self.db.session() as s:
            job = Job(key=key, started_at=stamp(), status="running")
            s.add(job)
            s.flush()
            identifier = job.id
        try:
            result = function()
            with self.db.session() as s:
                j = s.get(Job, identifier)
                j.status, j.finished_at, j.detail = "success", stamp(), canonical(result)
            return result
        except Exception as exc:
            # Provider exceptions are deliberately sanitised, never include URLs with credentials.
            detail = str(exc) if isinstance(exc, ValueError) or exc.__class__.__name__ == "ProviderError" else exc.__class__.__name__
            with self.db.session() as s:
                j = s.get(Job, identifier)
                j.status, j.finished_at, j.detail = "failed", stamp(), detail[:1000]
            log.error("Job %s failed: %s", key, detail)
            raise

    def refresh(self, market=None, symbols=None):
        end = now_utc().date().isoformat()
        start = (now_utc().date() - timedelta(days=int(self.settings.history_years * 365.25))).isoformat()
        def load():
            successful, failed = [], []
            if (symbols is None and (self.settings.data_mode == "demo" or any(i.market == "US" for i in self.universe if market is None or i.market == market))) or symbols is not None and "USDAUD.FOREX" in symbols:
                try:
                    self.job("data:USDAUD.FOREX", lambda: {"dataset_id": self.save_dataset("USDAUD.FOREX", self.provider.fx(start, end))})
                except Exception:
                    failed.append("USDAUD.FOREX")
            for inst in self.universe:
                if market and inst.market != market:
                    continue
                if symbols is not None and inst.symbol not in symbols:
                    continue
                try:
                    cutoff = latest_closed(inst.market) if self.settings.data_mode != "demo" else "2025-12-31"
                    result = self.job(f"data:{inst.symbol}", lambda inst=inst, cutoff=cutoff: {"dataset_id": self.save_dataset(inst.symbol, self.provider.fetch(inst, start, cutoff))})
                    successful.append(inst.symbol)
                except Exception:
                    failed.append(inst.symbol)
            return {"successful": successful, "failed": failed, "mode": self.settings.data_mode}
        return self.job("refresh" + (f":{market}" if market else ""), load)

    def data_health(self):
        rows = []
        with self.db.session() as s:
            for inst in self.universe:
                ds = latest_dataset(s, inst.symbol, self.settings.data_mode)
                expected = latest_closed(inst.market, lag_minutes=self.settings.provider_lag_minutes)
                last = ds.payload["bars"][-1]["date"] if ds else None
                rows.append({"symbol": inst.symbol, "market": inst.market, "last_session": last, "expected_session": expected, "fetched_at": ds.fetched_at if ds else None, "status": "synthetic" if ds and ds.mode == "demo" else "unavailable" if ds is None else "stale" if last < expected else "available", "missing_sessions": len(ds.payload.get("missing_sessions", [])) if ds else None, "news_status": ds.payload.get("news_status") if ds else "unavailable", "dataset_id": ds.id if ds else None})
            jobs = [{"id": j.id, "key": j.key, "started_at": j.started_at, "finished_at": j.finished_at, "status": j.status, "detail": j.detail} for j in s.query(Job).order_by(Job.id.desc()).limit(50)]
            fx = latest_dataset(s, "USDAUD.FOREX", self.settings.data_mode)
        return {"mode": self.settings.data_mode, "provider": PROVIDER_INFO[self.settings.data_mode], "symbols": rows, "jobs": jobs, "fx": {"last_date": fx.payload["rates"][-1]["date"], "fetched_at": fx.fetched_at} if fx else None, "scheduler_enabled": self.settings.scheduler_enabled, "provider_lag_minutes": self.settings.provider_lag_minutes, "key_configured": bool(self.settings.eodhd_api_key.get_secret_value()) if self.settings.data_mode == "eodhd" else False, "key_required": self.settings.data_mode == "eodhd", "intraday": "EOD strategy engine. Optional Moomoo US quote monitor is local only and requires a successful access test"}

    def scan(self, market=None, record_forward=True):
        def work():
            created = stamp()
            candidates, unavailable, inputs = [], [], {}
            with self.db.session() as s:
                config = rules(s)
                for inst in self.universe:
                    if inst.role not in {"stock", "etf"} or market and inst.market != market:
                        continue
                    ds = latest_dataset(s, inst.symbol, self.settings.data_mode)
                    if not ds:
                        unavailable.append({"symbol": inst.symbol, "reason": "No cached data; configure and refresh provider"})
                        continue
                    frame = indicator_frame(ds.payload, config.strategy.lookback)
                    if len(frame) < 200:
                        unavailable.append({"symbol": inst.symbol, "reason": "Fewer than 200 observations; SMA200 warmup incomplete"})
                        continue
                    day = frame.index[-1]
                    benchmark = latest_dataset(s, inst.benchmark, self.settings.data_mode)
                    rs = None
                    if benchmark:
                        b_frame = indicator_frame(benchmark.payload)
                        if day in b_frame.index:
                            x, y = finite_row(frame.loc[day]), finite_row(b_frame.loc[day])
                            if x["return63"] is not None and y["return63"] is not None:
                                rs = x["return63"] - y["return63"]
                    c = rank_candidate(inst, finite_row(frame.iloc[-1]), rs, ds.payload.get("market_cap"), config.scanner, config.strategy)
                    expected = latest_closed(inst.market, lag_minutes=self.settings.provider_lag_minutes)
                    fresh = ds.mode != "demo" and day >= expected
                    missing_recent = any(d >= frame.index[max(0, len(frame) - 210)] for d in ds.payload.get("missing_sessions", []))
                    if ds.mode != "demo" and (not fresh or missing_recent):
                        c["qualified"] = False
                        c["exclusions"].append("Stale observations or missing recent sessions")
                    c.update({"asof": day, "fetched_at": ds.fetched_at, "dataset_id": ds.id, "benchmark_dataset_id": benchmark.id if benchmark else None, "mode": ds.mode, "data_status": "synthetic" if ds.mode == "demo" else "delayed EOD" if fresh else "stale", "news": ds.payload.get("news", []), "news_status": ds.payload.get("news_status"), "volume_basis": ds.payload.get("volume_basis")})
                    candidates.append(c)
                    inputs[inst.symbol] = ds.id
            candidates.sort(key=lambda c: (-c["score"], c["symbol"]))
            shortlist = [c for c in candidates if c["qualified"]][:10]
            payload = {"created_at": created, "mode": self.settings.data_mode, "universe_revision": self.universe_revision, "rules": config.model_dump(), "rule_version": RULE_VERSION, "candidates": candidates, "shortlist": shortlist, "unavailable": unavailable, "datasets": inputs, "notice": "Hypothetical setups from deterministic rules. Scores are not probabilities. No news sentiment is inferred."}
            identifier = str(uuid.uuid4())
            with self.db.session() as s:
                s.add(Scan(id=identifier, created_at=created, mode=self.settings.data_mode, payload=payload))
            if record_forward and self.settings.data_mode in {"yahoo", "eodhd"}:
                from .paper import record_signals
                record_signals(self, shortlist)
            return {"scan_id": identifier, **payload}
        return self.job("scan" + (f":{market}" if market else ""), work)

    def latest_scan(self):
        with self.db.session() as s:
            row = s.query(Scan).filter_by(mode=self.settings.data_mode).order_by(Scan.created_at.desc()).first()
            return {"scan_id": row.id, **row.payload} if row else {"candidates": [], "shortlist": [], "unavailable": [], "mode": self.settings.data_mode}

    def stock(self, symbol):
        inst = self.instrument(symbol)
        with self.db.session() as s:
            ds = latest_dataset(s, symbol, self.settings.data_mode)
            if not ds:
                raise ValueError("Data unavailable; run provider refresh")
            frame = indicator_frame(ds.payload)
            chart = [{"date": day, **finite_row(row)} for day, row in frame.iloc[-600:].iterrows()]
            return {"instrument": inst.model_dump(), "dataset_id": ds.id, "mode": ds.mode, "fetched_at": ds.fetched_at, "bars": chart, "actions": ds.payload["actions"], "news": ds.payload["news"], "news_status": ds.payload["news_status"], "volume_basis": ds.payload["volume_basis"], "source_provider": ds.provider, "source_symbol": ds.payload.get("source_symbol", ds.symbol), "price_basis": ds.payload.get("price_basis", "Raw provider OHLC"), "news_provider": "Google News RSS" if ds.mode == "yahoo" else ds.mode}

    def backtest(self, request):
        from .backtests import research_run
        with self.db.session() as s:
            prior = s.get(Backtest, request["rerun_id"]) if request.get("rerun_id") else None
            if request.get("rerun_id") and prior is None:
                raise ValueError("Saved backtest not found")
            if prior:
                m = prior.payload["manifest"]
                from .backtests import code_version
                if code_version() != m["code_sha256"]:
                    raise ValueError("Code version changed; rerun from the saved source revision to compare exactly")
                request = m["request"]
                inst = Instrument.model_validate(m["instrument"])
                ds, bench = s.get(Dataset, m["dataset_id"]), s.get(Dataset, m["benchmark_dataset_id"])
                fx = s.get(Dataset, m["fx_dataset_id"]) if m["fx_dataset_id"] else None
                from .domain import AppRules
                config = AppRules.model_validate(m["rules"])
            else:
                inst = self.instrument(request["symbol"])
                ds = s.get(Dataset, request["dataset_id"]) if request.get("dataset_id") else latest_dataset(s, inst.symbol, self.settings.data_mode)
                bench = latest_dataset(s, inst.benchmark, self.settings.data_mode)
                fx = latest_dataset(s, "USDAUD.FOREX", self.settings.data_mode)
                config = rules(s)
            if not ds or not bench or inst.market == "US" and not fx:
                raise ValueError("Stock, benchmark or FX dataset unavailable; refresh all required feeds")
            if ds.symbol != inst.symbol or ds.mode != self.settings.data_mode or bench.mode != ds.mode or fx and fx.mode != ds.mode:
                raise ValueError("Dataset symbol or mode mismatch")
        payload = research_run(ds, inst, fx, bench, request, config)
        identifier = str(uuid.uuid4())
        with self.db.session() as s:
            s.add(Backtest(id=identifier, created_at=stamp(), input_hash=digest(payload["manifest"]), payload=payload))
        return {"id": identifier, **payload, "rerun_matches": payload["result_hash"] == prior.payload["result_hash"] if prior else None}

    def report(self, weekly=False):
        from .paper import account_view
        scan = self.latest_scan()
        health = self.data_health()
        paper = account_view(self)
        current = now_utc().astimezone(__import__("zoneinfo").ZoneInfo("Australia/Melbourne"))
        key = current.strftime("%G-W%V") if weekly else current.date().isoformat()
        name = ("weekly-" if weekly else "daily-") + key
        output = self.settings.report_dir
        output.mkdir(parents=True, exist_ok=True)
        report = {"generated_at": stamp(), "report_type": "weekly" if weekly else "daily", "mode": self.settings.data_mode, "research": scan, "paper": paper, "health": health}
        # Atomic replacement; historical scan/run/signal evidence remains immutable in DB.
        temp = output / f"{name}.json.tmp"
        temp.write_text(json.dumps(report, indent=2, allow_nan=False), encoding="utf-8")
        temp.replace(output / f"{name}.json")
        lines = [f"# MarketLens {'weekly strategy' if weekly else 'daily research'} report · {key}", "", f"Mode: {self.settings.data_mode}. Research and simulation only.", "", f"Forward signals: {paper['milestone']['generated']}; evaluable: {paper['milestone']['evaluable']} / 100.", "", "## Qualifying hypothetical setups", ""]
        lines += [f"- {c['symbol']} · rule score {c['score']:.1f}/100 · {', '.join(c['setups'])} · as of {c['asof']} ({c['data_status']})" for c in scan["shortlist"]] or ["No qualifying setups."]
        lines += ["", "## Forward performance", "", canonical(paper["metrics"]), "", "## Strategy/market breakdown", "", canonical(paper["breakdown"]), "", "## Data exceptions", ""]
        lines += [f"- {r['symbol']}: {r['status']} · latest {r['last_session']}" for r in health["symbols"] if r["status"] != "available"]
        lines += ["", "100 is a monitoring milestone, not evidence of significance. News unavailable where stated. Benchmark-relative forward performance remains unavailable until a matching dated benchmark snapshot exists."]
        (output / f"{name}.md").write_text("\n".join(lines), encoding="utf-8")
        return {"json": f"{name}.json", "markdown": f"{name}.md"}
