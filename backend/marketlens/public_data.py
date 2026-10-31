"""No-key Yahoo daily history and Google News RSS, for personal research.

These public interfaces have no contracted availability. A failed request never
returns fixture prices, invented news, or another provider's data.
"""
import copy
import math
import time
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from urllib.parse import quote, urlparse
from xml.etree import ElementTree
from zoneinfo import ZoneInfo

import httpx

from .calendars import session_times
from .domain import Bar, now_utc, stamp
from .providers import ProviderError


def yahoo_symbol(instrument):
    base = instrument.symbol.rsplit(".", 1)[0]
    return base + ".AX" if instrument.market == "ASX" else base.replace(".", "-")


class PublicHTTP:
    def __init__(self, interval=2):
        self.interval = interval
        self.last_request = 0.0

    def get(self, url, params=None):
        for attempt in range(3):
            delay = self.interval - (time.monotonic() - self.last_request)
            if delay > 0:
                time.sleep(delay)
            self.last_request = time.monotonic()
            try:
                response = httpx.get(url, params=params, timeout=30, follow_redirects=True,
                                     headers={"User-Agent": "MarketLens/1.0 (personal end-of-day research)",
                                              "Accept": "application/json,application/rss+xml,application/xml"})
            except httpx.TransportError:
                if attempt == 2:
                    raise ProviderError("Public data connection failed after three attempts") from None
                time.sleep(2 ** attempt)
                continue
            if response.status_code == 429:
                raise ProviderError("Public feed rate limited (HTTP 429); refresh later. No provider substitution was made")
            if response.status_code in {500, 502, 503, 504} and attempt < 2:
                time.sleep(2 ** attempt)
                continue
            if response.status_code != 200:
                raise ProviderError(f"Public feed HTTP {response.status_code}; source unavailable")
            if len(response.content) > 12_000_000:
                raise ProviderError("Provider response exceeds the allowed size")
            return response
        raise ProviderError("Public data request failed")


class GoogleNewsRSS:
    def __init__(self, get):
        self.get = get
        self.cache = {}

    def fetch(self, instrument, limit=10):
        cached = self.cache.get(instrument.symbol)
        if cached and time.monotonic() - cached[0] < 1800:
            return copy.deepcopy(cached[1])
        base = instrument.symbol.rsplit(".", 1)[0]
        query = f'"{base}" {"ASX" if instrument.market == "ASX" else "US"} stock when:7d'
        try:
            response = self.get("https://news.google.com/rss/search", {"q": query, "hl": "en-AU", "gl": "AU", "ceid": "AU:en"})
            content = response.content
            if len(content) > 2_000_000 or b"<!DOCTYPE" in content.upper() or b"<!ENTITY" in content.upper():
                raise ProviderError("Unsupported RSS document")
            root = ElementTree.fromstring(content)
            if root.tag != "rss" or root.find("channel") is None:
                raise ProviderError("Expected a Google News RSS channel")
            retrieved = now_utc()
            articles, seen = [], set()
            for item in root.findall("./channel/item"):
                try:
                    published = parsedate_to_datetime(item.findtext("pubDate", ""))
                    if published.tzinfo is None:
                        continue
                    published = published.astimezone(timezone.utc)
                    if not retrieved - timedelta(days=7) <= published <= retrieved:
                        continue
                    title = " ".join(unescape(item.findtext("title", "")).split())[:1000]
                    link = item.findtext("link", "").strip()
                    parsed = urlparse(link)
                    if not title or parsed.scheme not in {"http", "https"} or not parsed.hostname or link in seen:
                        continue
                    seen.add(link)
                    publisher = " ".join((item.findtext("source") or "Publisher not supplied").split())[:200]
                    articles.append({"title": title, "url": link, "published_at": stamp(published),
                                     "source": publisher, "feed_provider": "Google News RSS", "fetched_at": stamp(retrieved)})
                except (TypeError, ValueError, OverflowError):
                    continue
            articles.sort(key=lambda n: n["published_at"], reverse=True)
            result = {"news": articles[:limit], "news_status": "available" if articles else "unavailable: no dated headlines within the past seven days", "news_fetched_at": stamp(retrieved)}
            self.cache[instrument.symbol] = (time.monotonic(), result)
            return copy.deepcopy(result)
        except (ProviderError, ElementTree.ParseError) as exc:
            return {"news": [], "news_status": f"unavailable: Google News RSS ({str(exc)[:160]})", "news_fetched_at": stamp()}


def local_day(timestamp, zone):
    return datetime.fromtimestamp(int(timestamp), timezone.utc).astimezone(ZoneInfo(zone)).date().isoformat()


def normalize_yahoo_chart(result, instrument, start, end, retrieved=None):
    """Restore as-traded OHLCV from Yahoo's split-adjusted history.

    All effective split events through retrieval are required, even when the
    requested backtest end is earlier. Dividend-adjusted close is display-only.
    This reconstruction does not establish historical point-in-time vintages.
    """
    retrieved = retrieved or now_utc()
    expected_currency = "AUD" if instrument.market == "ASX" else "USD"
    meta = result.get("meta", {})
    if meta.get("currency") != expected_currency:
        raise ProviderError("Yahoo listing currency does not match the configured instrument")
    if meta.get("symbol") != yahoo_symbol(instrument):
        raise ProviderError("Yahoo returned a different instrument symbol")
    zone = meta.get("exchangeTimezoneName") or ("Australia/Sydney" if instrument.market == "ASX" else "America/New_York")
    events = result.get("events", {})
    splits = []
    for item in events.get("splits", {}).values():
        try:
            numerator, denominator = item.get("numerator"), item.get("denominator")
            if numerator is None or denominator is None:
                numerator, denominator = str(item["splitRatio"]).replace("/", ":").split(":")
            ratio = float(numerator) / float(denominator)
            day = local_day(item["date"], zone)
            if not math.isfinite(ratio) or ratio <= 0:
                raise ValueError("Invalid split ratio")
            if day <= retrieved.astimezone(ZoneInfo(zone)).date().isoformat():
                splits.append({"kind": "split", "date": day, "ratio": ratio})
        except (KeyError, ValueError, TypeError, ZeroDivisionError):
            raise ProviderError("Yahoo split event is invalid; unsafe price reconstruction refused") from None
    splits.sort(key=lambda a: a["date"])
    def later_factor(day):
        return math.prod(a["ratio"] for a in splits if a["date"] > day)
    actions = list(splits)
    for item in events.get("dividends", {}).values():
        try:
            day = local_day(item["date"], zone)
            amount = float(item["amount"]) * later_factor(day)
            if not math.isfinite(amount) or amount < 0:
                raise ValueError("Invalid cash amount")
            if start <= day <= end:
                actions.append({"kind": "dividend", "date": day, "amount": amount, "payment_date": None,
                                "currency": expected_currency, "source_amount_basis": "Yahoo split-adjusted dividend restored with later split factors"})
        except (KeyError, ValueError, TypeError):
            raise ProviderError("Yahoo dividend event is invalid") from None
    quote_rows = (result.get("indicators", {}).get("quote") or [{}])[0]
    adjusted = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose", [])
    bars, source_bars, missing = [], [], []
    for index, timestamp in enumerate(result.get("timestamp", [])):
        day = local_day(timestamp, zone)
        if not start <= day <= end:
            continue
        try:
            _, close_time = session_times(instrument.market, day)
            if close_time > retrieved:
                continue
            original = {key: quote_rows[key][index] for key in ("open", "high", "low", "close", "volume")}
            if any(value is None for value in original.values()):
                raise ValueError("Missing OHLCV")
            original = {key: float(value) for key, value in original.items()}
            adjusted_close = float(adjusted[index]) if index < len(adjusted) and adjusted[index] is not None else None
            factor = later_factor(day)
            restored = {key: original[key] * factor for key in ("open", "high", "low", "close")}
            bar = Bar(date=day, **restored, volume=original["volume"] / factor, adjusted_close=adjusted_close)
            bars.append(bar.model_dump())
            source_bars.append({"date": day, **original, "adjusted_close": adjusted_close, "restoration_factor": factor, "source_timestamp": int(timestamp)})
        except (KeyError, IndexError, TypeError, ValueError):
            missing.append(day)
    if not bars:
        raise ProviderError("Yahoo returned no valid completed daily observations")
    # Yahoo can append a quote snapshot to an already complete daily bar. Its
    # volume can differ. Prefer the bar timestamped at the official session open;
    # never add the two volumes or choose an arbitrary conflicting observation.
    grouped = {}
    for bar, source in zip(bars, source_bars):
        grouped.setdefault(bar["date"], []).append((bar, source))
    selected, duplicates = [], []
    for day, observations in grouped.items():
        chosen = observations[0]
        if len(observations) > 1:
            opened = int(session_times(instrument.market, day)[0].timestamp())
            daily = [pair for pair in observations if pair[1]["source_timestamp"] == opened]
            if len(daily) == 1:
                chosen = daily[0]
            elif not all(pair[0] == chosen[0] for pair in observations):
                raise ProviderError("Yahoo supplied conflicting daily bars without one canonical session-open record")
            duplicates.append({"date": day, "selected_timestamp": chosen[1]["source_timestamp"],
                               "discarded_timestamps": [pair[1]["source_timestamp"] for pair in observations if pair is not chosen],
                               "rule": "Prefer unique official-session-open daily bar over appended quote; identical duplicates may collapse"})
        selected.append(chosen[0])
    bars = selected
    days = [b["date"] for b in bars]
    if days != sorted(set(days)):
        raise ProviderError("Yahoo dates are duplicated or unordered")
    return {"bars": bars, "source_bars": source_bars, "duplicate_source_sessions": duplicates, "actions": sorted(actions, key=lambda a: (a["date"], a["kind"])),
            "actions_status": "Yahoo chart events; dividend payment dates unavailable, ex-date cash approximation",
            "market_cap": None, "currency": expected_currency, "market": instrument.market,
            "source_symbol": yahoo_symbol(instrument), "source_regular_market_time": meta.get("regularMarketTime"),
            "price_basis": "As-traded OHLC reconstructed from Yahoo split-adjusted history; adjusted_close is never executable",
            "volume_basis": "as-traded volume reconstructed from Yahoo split-adjusted volume",
            "adjustment_warning": "Restated vendor history, not a historical availability archive. Source split/dividend completeness is not guaranteed.",
            "invalid_source_sessions": missing, "label": "Actual Yahoo Finance daily observations; delayed/unofficial, personal research only"}


class YahooProvider:
    def __init__(self, settings, db=None):
        self.settings = settings
        self.http = PublicHTTP(settings.yahoo_request_interval_seconds)
        self.news = GoogleNewsRSS(self.http.get)

    def chart(self, symbol, start):
        # Fetch through retrieval so later splits can be undone correctly for an earlier end date.
        period1 = int(datetime.combine(date.fromisoformat(start), datetime.min.time(), timezone.utc).timestamp()) - 86400
        response = self.http.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(symbol, safe='')}",
                                 {"period1": period1, "period2": int(now_utc().timestamp()), "interval": "1d", "events": "div,splits", "includePrePost": "false"})
        try:
            payload = response.json()["chart"]
            if payload.get("error") or not payload.get("result"):
                raise ValueError("Chart unavailable")
            return payload["result"][0]
        except (ValueError, KeyError, TypeError, IndexError):
            raise ProviderError("Yahoo chart response is unavailable or invalid") from None

    def fetch(self, instrument, start, end):
        payload = normalize_yahoo_chart(self.chart(yahoo_symbol(instrument), start), instrument, start, end)
        news = self.news.fetch(instrument) if self.settings.enable_news else {"news": [], "news_status": "disabled: ENABLE_NEWS=false"}
        return {**payload, **news}

    def fx(self, start, end):
        result = self.chart("AUDUSD=X", start)
        if result.get("meta", {}).get("currency") != "USD" or result.get("meta", {}).get("symbol") != "AUDUSD=X":
            raise ProviderError("Yahoo AUDUSD=X quote currency is ambiguous")
        close = (result.get("indicators", {}).get("quote") or [{}])[0].get("close", [])
        end = min(end, (now_utc().date() - timedelta(days=1)).isoformat())
        rates = []
        for index, timestamp in enumerate(result.get("timestamp", [])):
            day = local_day(timestamp, "UTC")
            if not start <= day <= end:
                continue
            if index >= len(close) or close[index] is None:
                continue
            value = float(close[index])
            if not math.isfinite(value) or value <= 0:
                raise ProviderError("Yahoo FX observation is invalid")
            rates.append({"date": day, "rate": 1 / value})
        if not rates:
            raise ProviderError("Yahoo daily FX is unavailable")
        return {"rates": rates, "pair": "USDAUD", "source_symbol": "AUDUSD=X", "conversion": "Inverse of observed USD per AUD",
                "label": "Actual Yahoo daily FX, AUD per USD; prior-date rates only, maximum age seven days"}
