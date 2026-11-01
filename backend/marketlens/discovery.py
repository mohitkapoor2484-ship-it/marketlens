"""Bounded Yahoo candidate discovery, isolated from strategy and paper evidence."""
import math
import re
import time
from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, model_validator

from .calendars import latest_closed
from .config import ROOT
from .db import DiscoveryRun, UniverseRevision, latest_dataset
from .domain import Instrument, StrictModel, digest, now_utc, parse_time, stamp
from .providers import ProviderError
from .universe import save_universe
from .fundamentals import ProfileRateLimit, analyst, enrich_rows, filter_failure

VERSION = "yahoo-discovery-2"
EXCHANGES = {"ASX": ["ASX"], "US": ["NMS", "NYQ", "NGM", "NCM", "ASE", "PCX", "BTS"]}
PRESETS = {"ASX": {"min_price": .3, "min_average_volume": 100_000, "min_turnover": 1_000_000},
           "US": {"min_price": 5., "min_average_volume": 300_000, "min_turnover": 5_000_000}}
INFO = {"name": "Yahoo Finance screener via yfinance", "key_required": False,
        "documentation": "https://ranaroussi.github.io/yfinance/reference/api/yfinance.screen.html",
        "coverage": "ASX and selected major US exchanges; equities and ETFs classified by Yahoo. OTC excluded. Bounded sample, not an entire-exchange scan.",
        "delay": "Provider quote snapshots; delay and completeness not guaranteed. Setup analysis uses separately downloaded completed daily bars.",
        "limits": "Up to 250 per asset type per market; identical requests cached for 30 minutes. No published guaranteed request quota.",
        "licence": "Unofficial client; personal research use. No exchange licence or redistribution rights are supplied by yfinance.",
        "notice": "Discovery statistics are not setup scores. ETF leverage, inverse exposure and holdings are not verified."}


class DiscoveryRequest(StrictModel):
    market: Literal["ASX", "US"] = "ASX"
    kind: Literal["all", "stock", "etf"] = "all"
    limit: int = Field(250, ge=25, le=250)
    min_price: float | None = Field(None, ge=.01, le=100_000)
    min_average_volume: float | None = Field(None, ge=0, le=1e12)
    min_turnover: float | None = Field(None, ge=0, le=1e15)
    min_market_cap: float | None = Field(None, ge=0, le=1e16)
    max_market_cap: float | None = Field(None, ge=0, le=1e16)
    min_roe_percent: float | None = Field(None, ge=-100000, le=100000)
    max_roe_percent: float | None = Field(None, ge=-100000, le=100000)
    analyst_rating: Literal['any','rated','strong_buy','buy_or_better','hold_or_better','underperform_or_sell'] = 'any'

    @model_validator(mode='after')
    def ordered_ranges(self):
        for low, high in ((self.min_market_cap, self.max_market_cap), (self.min_roe_percent, self.max_roe_percent)):
            if low is not None and high is not None and low > high:
                raise ValueError('Minimum cannot exceed maximum for market cap or ROE')
        return self

    def effective(self):
        value = self.model_dump()
        result = {k: PRESETS[self.market].get(k) if v is None else v for k, v in value.items()}
        for key in PRESETS[self.market]:
            result[key] = float(result[key])
        return result


class ImportRequest(StrictModel):
    run_id: int = Field(ge=1)
    symbols: list[str] = Field(min_length=1, max_length=20)
    expected_revision: int = Field(ge=1)


class YahooDiscovery:
    def __init__(self, settings):
        self.settings = settings
        self.last_request = 0.

    def pace(self):
        delay = max(2, self.settings.yahoo_request_interval_seconds) - (time.monotonic() - self.last_request)
        if delay > 0:
            time.sleep(delay)
        self.last_request = time.monotonic()

    def fundamentals(self, symbol):
        import yfinance as yf
        from yfinance.exceptions import YFRateLimitError
        self.pace()
        try:
            return yf.Ticker(symbol).get_info()
        except YFRateLimitError:
            raise ProfileRateLimit('Yahoo profile requests rate limited') from None
        except Exception:
            raise ProviderError('Yahoo company profile unavailable') from None

    def screen(self, request, kind):
        # Lazy import keeps fixture operation independent of cookies and networking.
        import yfinance as yf
        yf.set_tz_cache_location(str(ROOT / "data/yahoo-cache"))
        Q = yf.EquityQuery if kind == "stock" else yf.ETFQuery
        conditions = [Q("eq", ["region", "au" if request["market"] == "ASX" else "us"]),
                          Q("is-in", ["exchange", *EXCHANGES[request["market"]]]),
                          Q("gte", ["intradayprice", request["min_price"]]),
                          Q("gte", ["avgdailyvol3m", request["min_average_volume"]])]
        if kind == 'stock':
            for key, op in [('min_market_cap','gte'),('max_market_cap','lte')]:
                if request.get(key) is not None:
                    conditions.append(Q(op, ['intradaymarketcap', request[key]]))
        query = Q('and', conditions)
        self.pace()
        try:
            result = yf.screen(query, size=request["limit"], sortField="avgdailyvol3m", sortAsc=False)
        except Exception:
            raise ProviderError("Yahoo discovery unavailable or rate limited. No replacement source or fixture data used") from None
        if not isinstance(result, dict) or not isinstance(result.get("quotes"), list) or len(result["quotes"]) > request["limit"]:
            raise ProviderError("Yahoo discovery returned an unsupported response")
        return result


def number(row, key, required=False):
    value = row.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        if required:
            raise ValueError("Missing or invalid price, volume or source time")
        return None
    return float(value)


def normalize(row, request, kind, retrieved):
    market = request["market"]
    source_symbol = row.get("symbol", "")
    if not isinstance(source_symbol, str) or not re.fullmatch(r"[A-Z0-9][A-Z0-9.\-]{0,19}", source_symbol):
        raise ValueError("Unsupported symbol")
    if row.get("exchange") not in EXCHANGES[market] or row.get("currency") != ("AUD" if market == "ASX" else "USD"):
        raise ValueError("Wrong exchange or currency")
    if row.get("quoteType") != ("EQUITY" if kind == "stock" else "ETF"):
        raise ValueError("Wrong security type")
    if market == "ASX":
        if not source_symbol.endswith(".AX"):
            raise ValueError("Missing ASX suffix")
        symbol = source_symbol[:-3] + ".AU"
    else:
        if "." in source_symbol:  # Yahoo US share classes use a hyphen.
            raise ValueError("Foreign listing or ambiguous US suffix")
        symbol = source_symbol.replace("-", ".") + ".US"
    inst = Instrument(symbol=symbol, name=str(row.get("longName") or row.get("shortName") or symbol).strip()[:150],
                      market=market, benchmark="STW.AU" if market == "ASX" else "SPY.US", role=kind)
    price, average, volume = (number(row, k, True) for k in ("regularMarketPrice", "averageDailyVolume3Month", "regularMarketVolume"))
    if price <= 0 or average <= 0:
        raise ValueError("Missing or invalid price, volume or source time")
    source = datetime.fromtimestamp(number(row, "regularMarketTime", True), timezone.utc)
    if source > retrieved + timedelta(minutes=5):
        raise ValueError("Future source timestamp")
    turnover = price * average
    if price < request["min_price"] or average < request["min_average_volume"] or turnover < request["min_turnover"]:
        raise ValueError("Below price, average-volume or approximate-turnover filter")
    fifty, two_hundred = number(row, "fiftyDayAverage"), number(row, "twoHundredDayAverage")
    return {"instrument": inst.model_dump(), "symbol": symbol, "source_symbol": source_symbol, "name": inst.name,
            "market": market, "kind": kind, "exchange": row["exchange"], "currency": row["currency"],
            "quote_price": price, "volume": volume, "average_volume_3m": average,
            "approximate_turnover": turnover, "relative_volume_3m": volume / average,
            "change_percent": number_signed(row, "regularMarketChangePercent"),
            'market_cap': number(row, 'marketCap') or None, 'market_cap_currency': row['currency'],
            'market_cap_source': 'Yahoo screener quote', 'market_cap_fetched_at': stamp(retrieved),
            **analyst(row), 'analyst_source': 'Yahoo screener quote', 'analyst_fetched_at': stamp(retrieved),
            'roe_percent': None, 'fundamental_fetched_at': None, 'reported_quarter': None,
            "sma50_quote": fifty, "sma200_quote": two_hundred,
            "above_sma50": price > fifty if fifty else None, "above_sma200": price > two_hundred if two_hundred else None,
            "source_at": stamp(source), "fetched_at": stamp(retrieved),
            "reason": f"Passes {request['min_price']:g} price, {request['min_average_volume']:g} average-volume and {request['min_turnover']:g} approximate-turnover minimums; ordered by relative volume. Full EOD setup analysis pending."}


def number_signed(row, key):
    value = row.get(key)
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def source_status(row, lag=180):
    day = parse_time(row["source_at"]).astimezone(ZoneInfo("Australia/Sydney" if row["market"] == "ASX" else "America/New_York")).date().isoformat()
    expected = latest_closed(row["market"], lag_minutes=lag)
    return "current" if day == expected else "stale" if day < expected else "session not yet eligible"


def run_discovery(service, body):
    if not service.settings.discovery_enabled:
        raise ValueError("Candidate discovery is disabled in .env")
    if service.settings.data_mode != "yahoo":
        raise ValueError("Broad discovery requires explicitly selected Yahoo mode; fixture and EODHD modes do not substitute providers")
    request = body.effective()
    request['fundamental_profile_budget'] = service.settings.discovery_fundamental_budget
    fingerprint = digest({"request": request, "version": VERSION})
    with service.db.session() as session:
        prior = session.query(DiscoveryRun).filter_by(mode="yahoo", request_hash=fingerprint).order_by(DiscoveryRun.id.desc()).first()
        if prior and (now_utc() - parse_time(prior.created_at)).total_seconds() < 1800:
            if prior.status == "failed":
                raise ProviderError("This discovery request failed recently; retry after the 30-minute cooldown. Previous successful results remain available")
            return {"run_id": prior.id, "cached": True, **prior.payload}
    def work():
        retrieved, rows, groups, errors = now_utc(), [], [], []
        for kind in ("stock", "etf") if request["kind"] == "all" else (request["kind"],):
            skipped, seen = {}, set()
            try:
                response = service.discovery_provider.screen(request, kind)
                quotes = response["quotes"]
                for quote in quotes:
                    try:
                        if not isinstance(quote, dict):
                            raise ValueError("Invalid row")
                        item = normalize(quote, request, kind, retrieved)
                        if item["symbol"] in seen:
                            raise ValueError("Duplicate symbol")
                        seen.add(item["symbol"])
                        # Profiles are checked after cheap quote/liquidity validation.
                        rows.append(item)
                    except (ValueError, TypeError, OverflowError, OSError) as exc:
                        reason = str(exc) if isinstance(exc, ValueError) else "Invalid row"
                        skipped[reason] = skipped.get(reason, 0) + 1
                sample = [r for r in rows if r['kind'] == kind]
                rows = [r for r in rows if r['kind'] != kind]
                sample.sort(key=lambda r: (-r['relative_volume_3m'], -r['approximate_turnover'], r['symbol']))
                fundamentals = enrich_rows(service, sample, retrieved)
                accepted = 0
                for item in sample:
                    reason = filter_failure(item, request)
                    if reason:
                        skipped[reason] = skipped.get(reason, 0) + 1
                    else:
                        checked = []
                        if request['min_market_cap'] is not None or request['max_market_cap'] is not None:
                            checked.append(f"market cap {item['currency']} {item['market_cap']:g}")
                        if request['min_roe_percent'] is not None or request['max_roe_percent'] is not None:
                            checked.append(f"ROE {item['roe_percent']:.2f}%")
                        if request['analyst_rating'] != 'any':
                            checked.append('Yahoo analyst category ' + item['analyst_rating'].replace('_',' '))
                        if checked:
                            item['reason'] += ' Passes active company filters: ' + ', '.join(checked) + '.'
                        rows.append(item)
                        accepted += 1
                total = response.get("total")
                total = total if isinstance(total, int) and not isinstance(total, bool) and total >= len(quotes) else None
                groups.append({"kind": kind, "status": "success", "provider_matches": total, "returned": len(quotes),
                               "accepted": accepted, 'fundamentals': fundamentals, "skipped": skipped, "capped": total > len(quotes) if total is not None else None})
            except Exception:
                message = f"Yahoo {kind} discovery unavailable. Previous results retained; no synthetic fallback"
                errors.append(message)
                groups.append({"kind": kind, "status": "failed", "error": message})
        rows.sort(key=lambda r: (-r["relative_volume_3m"], -r["approximate_turnover"], r["symbol"]))
        result = {"created_at": stamp(retrieved), "request": request, "version": VERSION, "mode": "yahoo",
                  "status": "partial" if errors and len(errors) < len(groups) else "failed" if errors else "success",
                  "groups": groups, "rows": rows, "errors": errors}
        with service.db.session() as session:
            saved = DiscoveryRun(created_at=result["created_at"], mode="yahoo", market=request["market"],
                                 request_hash=fingerprint, status=result["status"], payload=result)
            session.add(saved)
            session.flush()
            identifier = saved.id
        if result["status"] == "failed":
            raise ProviderError("Yahoo discovery failed; inspect Discover Candidates for the saved attempt")
        # Keep job logs small; the immutable run holds the full research snapshot.
        return {"run_id": identifier, "status": result["status"], "candidates": len(rows)}
    completed = service.job("discovery:" + request["market"], work)
    with service.db.session() as session:
        payload = session.get(DiscoveryRun, completed["run_id"]).payload
    return {"run_id": completed["run_id"], "cached": False, **payload}


def discovery_view(service):
    markets = []
    with service.db.session() as session:
        revision = session.query(UniverseRevision).order_by(UniverseRevision.id.desc()).first()
        members = {i['symbol'] for i in revision.payload}
        for market in ("ASX", "US"):
            q = session.query(DiscoveryRun).filter_by(mode=service.settings.data_mode, market=market)
            attempt = q.order_by(DiscoveryRun.id.desc()).first()
            saved = q.filter(DiscoveryRun.status.in_(["success", "partial"])).order_by(DiscoveryRun.id.desc()).first()
            if not attempt:
                markets.append({"market": market, "status": "not_run", "rows": []})
                continue
            payload = saved.payload if saved else {"rows": []}
            rows = [{**r, "in_universe": r["symbol"] in members, "quote_status": source_status(r, service.settings.provider_lag_minutes)} for r in payload["rows"]]
            markets.append({"market": market, "run_id": saved.id if saved else None,
                            **{k: v for k, v in payload.items() if k != "rows"}, "rows": rows,
                            "last_attempt": {"at": attempt.created_at, "status": attempt.status, "errors": attempt.payload["errors"]},
                            "status": attempt.status})
    return {"provider": INFO, "enabled": service.settings.discovery_enabled, "mode": service.settings.data_mode,
            "presets": PRESETS, "maximum_selection": 20, "markets": markets, "universe_revision": revision.id,
            'fundamentals': {'profile_budget': service.settings.discovery_fundamental_budget, 'cache_hours':24,
                             'notice': 'Company profiles are a bounded verified subset; unknown fields fail active filters. ETF assets are not substituted for market cap, and ETF fund ratings are not stock analyst consensus.'}}


def import_candidates(service, body):
    if service.settings.data_mode != "yahoo":
        raise ValueError("Import requires explicitly selected Yahoo mode")
    symbols = list(dict.fromkeys(body.symbols))
    with service.db.session() as session:
        saved = session.get(DiscoveryRun, body.run_id)
        if not saved or saved.mode != "yahoo" or saved.status not in {"success", "partial"}:
            raise ValueError("Select a successful Yahoo discovery run")
        rows = {r["symbol"]: r for r in saved.payload["rows"]}
    if any(symbol not in rows for symbol in symbols):
        raise ValueError("Selection is not part of the recorded discovery run")
    if any(source_status(rows[s], service.settings.provider_lag_minutes) != "current" for s in symbols):
        raise ValueError("Selected quotes are stale or their session is not yet eligible. Discover again after market close")
    existing = {i.symbol for i in service.universe}
    added = [Instrument.model_validate({**rows[s]["instrument"], "membership_evidence": f"discovery:{body.run_id}; observed {saved.created_at}; not historical PIT membership"}) for s in symbols if s not in existing]
    revision = save_universe(service, [i.model_dump() for i in service.universe + added], body.expected_revision)
    if not added:
        return {"added": [], "successful": [], "failed": [], "revision": revision["revision"], "notice": "Already in your universe; no duplicate instruments or downloads"}
    dependencies = {i.benchmark for i in added}
    if any(i.market == "US" for i in added):
        dependencies.add("USDAUD.FOREX")
    with service.db.session() as session:
        missing = [s for s in dependencies if not latest_dataset(session, s, "yahoo")]
    refreshed = service.refresh(symbols=[i.symbol for i in added] + sorted(missing))
    scan = service.scan(record_forward=False)
    return {"added": [i.symbol for i in added], **refreshed, "revision": revision["revision"], "scan_id": scan["scan_id"],
            "notice": "Saved to the research universe. Completed daily bars were analysed under current scanner rules. This import creates no paper signals; inspect exclusions and data health."}
