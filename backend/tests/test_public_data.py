import copy
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import httpx
import pytest

from marketlens.config import Settings
from marketlens.domain import Instrument
from marketlens.providers import ProviderError
from marketlens.public_data import GoogleNewsRSS, PublicHTTP, YahooProvider, normalize_yahoo_chart, yahoo_symbol

UTC = timezone.utc


def instrument(symbol="AAPL.US", market="US"):
    return Instrument(symbol=symbol, name="Apple" if market == "US" else "BHP", market=market, benchmark="SPY.US" if market == "US" else "STW.AU")


def epoch(day):
    return int(datetime.fromisoformat(day).replace(tzinfo=UTC).timestamp())


def chart():
    return {"meta": {"symbol": "AAPL", "currency": "USD", "exchangeTimezoneName": "America/New_York"},
            "timestamp": [epoch("2020-08-27T13:30:00"), epoch("2020-08-28T13:30:00"), epoch("2020-08-31T13:30:00")],
            "indicators": {"quote": [{"open": [125, 126, 127], "high": [127, 128, 129], "low": [124, 125, 126], "close": [126, 127, 128], "volume": [400, 800, 1000]}],
                           "adjclose": [{"adjclose": [120, 121, 122]}]},
            "events": {"splits": {"split": {"date": epoch("2020-08-31T13:30:00"), "numerator": 4, "denominator": 1}},
                       "dividends": {"div": {"date": epoch("2020-08-28T13:30:00"), "amount": .2}}}}


def test_canonical_symbols_map_to_yahoo():
    assert yahoo_symbol(instrument("BHP.AU", "ASX")) == "BHP.AX"
    assert yahoo_symbol(instrument("BRK.B.US")) == "BRK-B"
    assert yahoo_symbol(instrument()) == "AAPL"


def test_yahoo_restores_ohlcv_and_dividends_without_using_adjusted_close():
    original = chart()
    result = normalize_yahoo_chart(original, instrument(), "2020-08-27", "2020-08-31", datetime(2020, 9, 1, tzinfo=UTC))
    assert result["bars"][0]["open"] == 500
    assert result["bars"][0]["close"] == 504
    assert result["bars"][0]["adjusted_close"] == 120
    assert result["bars"][0]["volume"] == 100
    assert result["bars"][-1]["close"] == 128
    assert result["bars"][-1]["volume"] == 1000
    dividend = next(a for a in result["actions"] if a["kind"] == "dividend")
    assert dividend["amount"] == .8
    assert dividend["payment_date"] is None
    from marketlens.research import tradable_bars
    assert tradable_bars(result)[0]["volume"] == 100  # No second volume adjustment.
    assert original == chart()


def test_end_date_before_split_still_restores_as_traded_history():
    result = normalize_yahoo_chart(chart(), instrument(), "2020-08-27", "2020-08-28", datetime(2020, 9, 1, tzinfo=UTC))
    assert len(result["bars"]) == 2
    assert result["bars"][0]["close"] == 504


def test_asx_date_uses_exchange_timezone_not_utc_date():
    data = {"meta": {"symbol": "BHP.AX", "currency": "AUD", "exchangeTimezoneName": "Australia/Sydney"},
            "timestamp": [epoch("2026-10-29T23:00:00")],
            "indicators": {"quote": [{"open": [40], "high": [41], "low": [39], "close": [40], "volume": [1000]}]}}
    result = normalize_yahoo_chart(data, instrument("BHP.AU", "ASX"), "2026-10-30", "2026-10-30", datetime(2026, 10, 31, tzinfo=UTC))
    assert result["bars"][0]["date"] == "2026-10-30"
    with pytest.raises(ProviderError, match="no valid completed"):
        normalize_yahoo_chart(data, instrument("BHP.AU", "ASX"), "2026-10-30", "2026-10-30", datetime(2026, 10, 30, 1, tzinfo=UTC))


def test_missing_prices_stay_missing_and_wrong_currency_rejected():
    data = chart()
    data["indicators"]["quote"][0]["low"][1] = None
    result = normalize_yahoo_chart(data, instrument(), "2020-08-27", "2020-08-31", datetime(2020, 9, 1, tzinfo=UTC))
    assert "2020-08-28" in result["invalid_source_sessions"]
    assert len(result["bars"]) == 2
    data["meta"]["currency"] = "GBP"
    with pytest.raises(ProviderError, match="currency"):
        normalize_yahoo_chart(data, instrument(), "2020-08-27", "2020-08-31")


def test_invalid_split_is_not_silently_ignored():
    data = chart()
    data["events"]["splits"]["split"]["denominator"] = 0
    with pytest.raises(ProviderError, match="split event"):
        normalize_yahoo_chart(data, instrument(), "2020-08-27", "2020-08-31")


def test_appended_quote_does_not_replace_or_double_count_canonical_daily_volume():
    data = chart()
    data["timestamp"].append(epoch("2020-08-31T20:15:00"))
    for key, values in data["indicators"]["quote"][0].items():
        values.append(900 if key == "volume" else values[-1])
    data["indicators"]["adjclose"][0]["adjclose"].append(122)
    result = normalize_yahoo_chart(data, instrument(), "2020-08-27", "2020-08-31", datetime(2020, 9, 1, tzinfo=UTC))
    assert len(result["bars"]) == 3
    assert result["bars"][-1]["volume"] == 1000
    assert len(result["source_bars"]) == 4
    assert result["duplicate_source_sessions"][0]["date"] == "2020-08-31"


def test_conflicting_duplicate_without_unique_canonical_bar_fails():
    data = chart()
    data["timestamp"].append(data["timestamp"][-1])
    for key, values in data["indicators"]["quote"][0].items():
        values.append(900 if key == "volume" else values[-1])
    data["indicators"]["adjclose"][0]["adjclose"].append(122)
    with pytest.raises(ProviderError, match="conflicting daily"):
        normalize_yahoo_chart(data, instrument(), "2020-08-27", "2020-08-31", datetime(2020, 9, 1, tzinfo=UTC))


def test_rss_keeps_real_times_links_and_publishers_and_filters_future(monkeypatch):
    now = datetime(2026, 10, 30, 12, tzinfo=UTC)
    monkeypatch.setattr("marketlens.public_data.now_utc", lambda: now)
    recent = format_datetime(now - timedelta(hours=1))
    future = format_datetime(now + timedelta(days=1))
    xml = f'''<rss><channel><item><title>Actual headline &amp; detail</title><link>https://news.google.com/rss/articles/one</link><pubDate>{recent}</pubDate><source>Actual publisher</source></item><item><title>Future</title><link>https://example.com/future</link><pubDate>{future}</pubDate></item><item><title>Undated</title><link>https://example.com/undated</link></item><item><title>Invalid link</title><link>javascript:alert(1)</link><pubDate>{recent}</pubDate></item></channel></rss>'''
    calls = []
    feed = GoogleNewsRSS(lambda *args: calls.append(args) or httpx.Response(200, content=xml.encode()))
    result = feed.fetch(instrument())
    assert len(result["news"]) == 1
    assert result["news"][0]["source"] == "Actual publisher"
    assert result["news"][0]["feed_provider"] == "Google News RSS"
    assert result["news"][0]["title"] == "Actual headline & detail"
    assert "sentiment" not in result["news"][0]
    assert feed.fetch(instrument()) == result
    assert len(calls) == 1


@pytest.mark.parametrize("content", [b"not xml", b"<html>blocked</html>", b'<!DOCTYPE rss [<!ENTITY x "payload">]><rss><channel/></rss>'])
def test_unavailable_rss_never_invents_articles(content):
    result = GoogleNewsRSS(lambda *a: httpx.Response(200, content=content)).fetch(instrument())
    assert result["news"] == []
    assert result["news_status"].startswith("unavailable")


def test_rate_limit_is_visible_and_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr("marketlens.public_data.httpx.get", lambda *a, **kw: calls.append(a) or httpx.Response(429))
    with pytest.raises(ProviderError, match="429"):
        PublicHTTP(interval=0).get("https://query1.finance.yahoo.com/test")
    assert len(calls) == 1


def test_fx_inverts_usd_per_aud_and_excludes_current_day(monkeypatch):
    provider = YahooProvider(Settings(_env_file=None, yahoo_request_interval_seconds=0))
    monkeypatch.setattr("marketlens.public_data.now_utc", lambda: datetime(2026, 10, 30, 12, tzinfo=UTC))
    monkeypatch.setattr(provider, "chart", lambda *a: {"meta": {"symbol": "AUDUSD=X", "currency": "USD"},
                        "timestamp": [epoch("2026-10-29T00:00:00"), epoch("2026-10-30T00:00:00")],
                        "indicators": {"quote": [{"close": [.625, .7]}]}})
    result = provider.fx("2026-10-01", "2026-10-30")
    assert result["rates"] == [{"date": "2026-10-29", "rate": 1.6}]


def test_price_failure_never_substitutes_demo(monkeypatch):
    provider = YahooProvider(Settings(_env_file=None, yahoo_request_interval_seconds=0))
    def fail(*a):
        raise ProviderError("Yahoo unavailable")
    monkeypatch.setattr(provider, "chart", fail)
    with pytest.raises(ProviderError, match="Yahoo unavailable"):
        provider.fetch(instrument(), "2020-01-01", "2020-12-31")
