# Provider contracts (checked 1 November 2026)

## Default: Yahoo Finance daily history + Google News RSS

`DATA_MODE=yahoo` selects the explicit Yahoo chart adapter in `public_data.py`. It uses Yahoo's public chart JSON interface directly, following the earlier ETF project's approach. The application does not require the yfinance package, a Yahoo API key or a paid account. The [yfinance source-use notice](https://ranaroussi.github.io/yfinance/) documents the same upstream Yahoo interface's unofficial and personal-research nature. No uptime, exchange entitlement, historical membership, correction vintage or complete corporate-action coverage is promised.

- Canonical ids remain BHP.AU / AAPL.US; the adapter maps to BHP.AX / AAPL. US share-class dots become hyphens. Currency mismatches fail validation.
- Daily history targets eight years by default. Only complete exchange sessions are accepted, using the exchange timezone; current partial bars are excluded.
- Source OHLCV are split-adjusted. Effective split events reconstruct as-traded prices, volume and dividends. Dividend-adjusted close is kept separately. Original observations, timestamps and restoration factors remain in the immutable dataset. Historical availability is not point-in-time verified.
- Yahoo can append a quote snapshot to the same daily session with a different volume. A unique official-session-open daily record is preferred and the discarded snapshot recorded; duplicate volumes are never summed. Ambiguous conflicting observations fail.
- AUDUSD=X supplies USD per AUD; its reciprocal is stored as AUD per USD. Fills require a prior-date rate at most seven calendar days old. Missing/stale FX remains unavailable.
- Missing OHLCV remain missing. Unknown market caps fail an enabled minimum-cap filter; this adapter does not provide verified market capitalization.
- Two-second request spacing is an application throttle, not a published Yahoo allowance. HTTP 429 fails visibly; transient connection/5xx failures have at most three attempts. No broker credentials, account login, proxy rotation or access-control bypass are implemented.

`ENABLE_NEWS=true` selects [Google News RSS](https://news.google.com/rss). No Google API key is required. This public RSS interface is not a contracted market-news API. Only actual titles, publisher names, links, pubDate values and retrieval timestamps are saved. Article bodies and sentiment are never invented. Future/undated/invalid items are rejected; headlines are limited to the last seven days. Duplicate links collapse. Successful feeds are cached for 30 minutes within a running process; EOD cycles request new news as needed. Empty feeds and failures are unavailable. Matches can be ambiguous, and RSS timestamps can differ from an article's original publication time.

**Personal use is distinct from public redistribution.** No source choice confers permission to publish raw market data. The public Pages workflow remains fixture-only and cannot export the local Yahoo database, portfolios, signals or headlines. Actual observations are used in the local/private research engine. External deployment requires appropriate access controls and data-display rights.

## Explicit demonstration mode

`DATA_MODE=demo` uses fixed deterministic synthetic data, no news and no network. It is labelled. Historical fixture replays never count as forward evidence. The Pages exporter always creates an isolated in-memory demo database, even when local/environment settings select Yahoo or EODHD.

## Optional legacy EODHD adapter

`DATA_MODE=eodhd` explicitly selects EODHD; it is never tried after a Yahoo failure. It requires an account/key and entitlements. [EOD API](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes), [symbols](https://eodhd.com/financial-apis/exchanges-api-list-of-tickers-and-trading-hours), [actions](https://eodhd.com/financial-apis/api-splits-dividends), [news](https://eodhd.com/financial-apis/stock-market-financial-news-api), [pricing](https://eodhd.com/pricing).

The advertised sandbox has 20 credits/day, 20 requests/minute and one year of history. EOD All-World is advertised at US$19.99/month; verify current terms. A 14-instrument refresh requires at least 43 credits, plus five credits per symbol for optional news and ten for fundamentals. Retries consume credits. These costs do not apply to Yahoo mode, and the application buys nothing.

Legacy fields are raw OHLC, adjusted close and split-adjusted volume. Personal plans are not blanket redistribution licences. Dividend currency mismatches and historical membership/delistings require additional verified data for stronger historical claims.
