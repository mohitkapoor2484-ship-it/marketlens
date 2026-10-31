"""Explicit adapters. No fallback from a real request to fixture data."""
import hashlib
import time
from datetime import date, timedelta
from typing import Protocol

import httpx
import numpy as np

from .calendars import latest_closed, sessions
from .db import Usage
from .domain import Bar, now_utc, parse_time, stamp

PROVIDER_INFO = {
    "yahoo": {"name": "Yahoo Finance + Google News RSS", "coverage": "Configured ASX .AX and US symbols; availability varies by listing. Daily AUD/USD from AUDUSD=X", "delay": "Completed daily sessions only; delayed source, no real-time guarantee", "adjustments": "Yahoo split-adjusted OHLCV restored to as-traded values with split events; dividend-adjusted close retained separately", "licence": "Personal research. Unofficial interfaces; no blanket public redistribution rights. Public Pages exports remain synthetic", "limits": "No API key or paid account required. No contracted request allowance or uptime. Two-second request spacing by default; rate limits stay visible", "url": "https://finance.yahoo.com/", "documentation": "https://ranaroussi.github.io/yfinance/", "news_documentation": "https://news.google.com/rss", "news_provider": "Google News RSS", "key_required": False},
    "demo": {"name": "Deterministic fixtures", "coverage": "Synthetic ASX/US sessions, 2017–2025", "delay": "Fixed historical fixture; never actual prices", "adjustments": "Synthetic split/dividend event fixtures", "licence": "Local test fixtures only", "limits": "No key, no network, never forward evidence", "url": None},
    "eodhd": {"name": "EOD Historical Data", "coverage": "AU / US equities and USDAUD.FOREX, subject to account entitlements", "delay": "End of day; configurable publication buffer, default 180 min", "adjustments": "Raw OHLC, split/dividend-adjusted close, split-adjusted volume; actions fetched separately", "licence": "Personal plans for personal use. Confirm entitlements and redistribution rights with vendor", "limits": "Sandbox: 20 credits/day, 20 requests/min, 1 year history. EOD: advertised US$19.99/mo. News costs 5 credits; fundamentals 10. Verify current terms", "url": "https://eodhd.com/pricing", "documentation": "https://eodhd.com/financial-apis/api-for-historical-data-and-volumes", "news_documentation": "https://eodhd.com/financial-apis/stock-market-financial-news-api"},
}


class ProviderError(RuntimeError):
    pass


class Provider(Protocol):
    def fetch(self, instrument, start: str, end: str) -> dict: ...
    def fx(self, start: str, end: str) -> dict: ...


class DemoProvider:
    def fetch(self, instrument, start, end):
        # Stable dates and random seed; changing wall clock never extends this fixture.
        seed = int(hashlib.sha256(instrument.symbol.encode()).hexdigest()[:8], 16)
        rng = np.random.default_rng(seed)
        days = sessions(instrument.market, "2017-01-03", "2025-12-31")
        price = 20 + seed % 80
        bars = []
        for i, day in enumerate(days):
            drift = 0.00025 + 0.0014 * np.sin(i / 31 + seed % 6)
            op = price * np.exp(rng.normal(0, .004))
            close = op * np.exp(drift + rng.normal(0, .012))
            high = max(op, close) * (1 + rng.uniform(.002, .018))
            low = min(op, close) * (1 - rng.uniform(.002, .018))
            volume = int(rng.uniform(700000, 3500000) * (2.5 if i % 43 == 0 else 1))
            bars.append(Bar(date=day, open=op, high=high, low=low, close=close, volume=volume, adjusted_close=close).model_dump())
            price = close
        return {"bars": bars, "actions": [], "actions_status": "fixture", "news": [], "news_status": "unavailable: fixture mode has no market news", "market_cap": None, "currency": "AUD" if instrument.market == "ASX" else "USD", "market": instrument.market, "volume_basis": "synthetic", "label": "SYNTHETIC DEMONSTRATION — not market observations"}

    def fx(self, start, end):
        import pandas as pd
        return {"rates": [{"date": d.date().isoformat(), "rate": float(1.5 + .08 * np.sin(i / 90))} for i, d in enumerate(pd.bdate_range("2017-01-03", "2025-12-31"))], "pair": "USDAUD", "label": "Synthetic AUD per USD fixture"}


class EODHDProvider:
    def __init__(self, settings, db):
        self.settings, self.db = settings, db
        self.last_request = 0.0

    def get(self, path, params=None, credits=1):
        key = self.settings.eodhd_api_key.get_secret_value()
        if not key or key.lower() == "demo":
            raise ProviderError("A personal EODHD key is required; shared demo tokens are not accepted as real-data operation")
        for attempt in range(3):
            wait = 60 / self.settings.eodhd_requests_per_minute - (time.monotonic() - self.last_request)
            if wait > 0:
                time.sleep(wait)
            with self.db.session() as s:
                from sqlalchemy import func
                day = now_utc().date().isoformat()
                used = s.query(func.coalesce(func.sum(Usage.credits), 0)).filter_by(day=day).scalar()
                if used + credits > self.settings.eodhd_daily_budget:
                    raise ProviderError("Configured daily API credit budget exhausted; no silent upgrade or provider substitution")
                s.add(Usage(day=day, credits=credits))
            self.last_request = time.monotonic()
            try:
                response = httpx.get(f"https://eodhd.com/api/{path}", params={**(params or {}), "api_token": key, "fmt": "json"}, timeout=30, follow_redirects=False)
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    continue
                if response.status_code != 200:
                    raise ProviderError(f"EODHD HTTP {response.status_code}; check plan, symbol, quota or provider status")
                payload = response.json()
                if isinstance(payload, dict) and ("error" in payload or "message" in payload):
                    raise ProviderError("Provider returned an error object")
                return payload
            except (httpx.TransportError, ValueError):
                if attempt == 2:
                    raise ProviderError("EODHD transport/JSON failure after three attempts") from None
        raise ProviderError("EODHD request failed")

    def fetch(self, instrument, start, end):
        rows = self.get(f"eod/{instrument.symbol}", {"from": start, "to": end, "period": "d", "order": "a"})
        if not isinstance(rows, list) or not rows:
            raise ProviderError("No OHLCV returned; entitlement or symbol may be unavailable")
        bars = [Bar.model_validate({k: row.get(k) for k in Bar.model_fields}).model_dump() for row in rows]
        actions = []
        # Fail closed: do not publish a price revision without its action feeds.
        for kind, endpoint in (("split", "splits"), ("dividend", "div")):
            events = self.get(f"{endpoint}/{instrument.symbol}", {"from": start, "to": end})
            if not isinstance(events, list):
                raise ProviderError("Corporate action feed unavailable")
            for row in events:
                if kind == "split":
                    numerator, denominator = str(row["split"]).split("/")
                    ratio = float(numerator) / float(denominator)
                    if not np.isfinite(ratio) or ratio <= 0:
                        raise ProviderError("Invalid split ratio")
                    actions.append({"kind": kind, "date": row["date"], "ratio": ratio})
                else:
                    # Never use the vendor's split-adjusted dividend in an as-traded ledger.
                    amount = row.get("unadjustedValue")
                    if amount is None:
                        raise ProviderError("As-traded dividend amount unavailable; cannot safely simulate cash dividends")
                    actions.append({"kind": kind, "date": row["date"], "payment_date": row.get("paymentDate"), "amount": float(amount), "currency": row.get("currency")})
        news, news_status = [], "disabled: enable_news requires news entitlement and credits"
        if self.settings.enable_news:
            try:
                items = self.get("news", {"s": instrument.symbol, "limit": 10, "from": (date.fromisoformat(end) - timedelta(days=7)).isoformat(), "to": end}, credits=5)
                if not isinstance(items, list):
                    raise ProviderError("Invalid news response")
                for n in items:
                    published = parse_time(n["date"])
                    if published <= now_utc() and str(n.get("link", "")).startswith(("https://", "http://")):
                        news.append({"title": str(n["title"])[:1000], "url": n["link"], "published_at": stamp(published), "source": "EODHD", "fetched_at": stamp()})
                news_status = "available" if news else "unavailable: no matching articles returned"
            except (ProviderError, ValueError, KeyError):
                news_status = "unavailable: news request failed; price feed remains usable"
        market_cap = None
        if self.settings.enable_fundamentals:
            fundamentals = self.get(f"fundamentals/{instrument.symbol}", {"filter": "Highlights::MarketCapitalization"}, credits=10)
            if isinstance(fundamentals, (float, int)) and np.isfinite(fundamentals) and fundamentals > 0:
                market_cap = float(fundamentals)
        return {"bars": bars, "actions": actions, "actions_status": "available", "news": news, "news_status": news_status, "market_cap": market_cap, "market_cap_asof": stamp() if market_cap else None, "currency": "AUD" if instrument.market == "ASX" else "USD", "market": instrument.market, "volume_basis": "vendor split-adjusted (not a historical point-in-time archive)", "label": "Actual provider EOD observations; delayed, subject to revisions"}

    def fx(self, start, end):
        end = min(end, (now_utc().date() - timedelta(days=1)).isoformat())
        rows = self.get("eod/USDAUD.FOREX", {"from": start, "to": end, "period": "d", "order": "a"})
        rates = []
        for row in rows:
            rate = float(row["close"])
            if not np.isfinite(rate) or rate <= 0:
                raise ProviderError("Invalid USD/AUD rate")
            rates.append({"date": row["date"], "rate": rate})
        if not rates:
            raise ProviderError("FX series unavailable")
        return {"rates": rates, "pair": "USDAUD", "label": "Actual EOD FX; strictly prior date rate used for fills, maximum age 7 days"}
