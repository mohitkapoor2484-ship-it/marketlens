# Provider contract (checked 1 November 2026)

Only two explicit adapters exist: deterministic fixtures and EODHD. `DATA_MODE` selects one. Network failures do not change modes. The `Provider` protocol in `providers.py` supports replacement; a new adapter must validate data, document timestamps, adjustments, volume basis, entitlements and costs, and pass the causality/fill tests.

EODHD official references:

- [EOD API and data conventions](https://eodhd.com/financial-apis/api-for-historical-data-and-volumes): raw OHLC, split/dividend-adjusted close, split-adjusted volume; adjusted series can be restated. Never trade adjusted close.
- [Exchange symbols and delisted lists](https://eodhd.com/financial-apis/exchanges-api-list-of-tickers-and-trading-hours): provider suffixes; active and delisted lists are separate. AU = Australian listing, US = consolidated US code. The app uses a maintained JSON universe, not an unverified full-history membership service.
- [Corporate actions](https://eodhd.com/financial-apis/api-splits-dividends): split ratios and dividend dates/amounts. As-traded dividend amount (`unadjustedValue`) is required; ingestion fails if it is absent rather than crediting a split-adjusted amount against raw shares.
- [News API](https://eodhd.com/financial-apis/stock-market-financial-news-api): provider headlines/links/dates, optional feed. No copied article bodies or inferred sentiment.
- [Pricing](https://eodhd.com/pricing): advertised personal sandbox 20 credits/day, 20 requests/min, one year of history; EOD All-World US$19.99/month; fundamentals US$59.99/month; all-in-one US$99.99/month. Plans, entitlement and prices can change. News is listed in the plan table but verify coverage/entitlement with the vendor. These are provider charges, not application charges, in USD and before any applicable taxes/currency conversion. The application purchases nothing.

The app itself applies a conservative configurable request rate and persistent UTC daily credit budget. EOD/splits/dividends/FX are budgeted ≥1 each, news 5 and fundamentals 10, including retry attempts. Current quota usage can also be inspected in SQLite `provider_usage`; vendor-side accounting is authoritative.

No guaranteed EOD publication time is assumed. Completed exchange sessions are checked against a default three-hour buffer. If a feed is missing or stale the UI says so; recent missing sessions disqualify forward candidates. Ten years of history is not guaranteed: configured target defaults to eight years and Data Health shows actual availability. Intraday is intentionally unsupported.

Listings may quote dividends in another currency; the current simulator rejects such action processing without explicit conversion data. Calendar, vendor revisions, delisting coverage and point-in-time membership require additional licensed sources for stronger historical claims. A current-universe backtest remains a conditional, survivor-selected instrument study.

Personal subscriptions are not blanket permission to publish, redistribute or commercialize exchange data. Confirm terms with the vendor before deployment. No browser scrapers or unofficial Yahoo fallback are included.
