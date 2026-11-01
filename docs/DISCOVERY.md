# Candidate discovery

MarketLens separates broad candidate discovery from EOD setup qualification.

Implementation: Yahoo screener adapter → validated, timestamped immutable discovery run → local/public candidate browser → explicit add and download → existing deterministic scanner. Discovery rows never generate paper signals. Yahoo daily history and Google News RSS remain the research providers. No TradingView service is silently substituted.

Defaults query ASX and major US exchanges, separately for equities and ETFs. Each query retrieves at most 250 rows, sorted by provider three-month average volume, then applies local price and approximate turnover filters. This is a bounded sample, not a complete exchange scan. Yahoo's reported match count and returned count are shown. Approximate turnover is quote price × provider three-month average volume, not the scanner's 20-session average dollar volume. Relative volume is quote volume ÷ provider three-month average volume. These discovery statistics are not setup scores or historical evidence.

Discovery runs are append-only and preserve the request, normalization version, source timestamps, counts, skipped-row reasons and normalized source values. Identical requests reuse a run for 30 minutes, including after restart. A failed refresh retains previous successful rows and shows the failed attempt. Stale quotes cannot be selected for import. Quotes timestamped more than five minutes in the future are rejected. Missing required fields, wrong exchanges/currencies/types and duplicate symbols are rejected. ETF classification follows Yahoo; leverage, inverse exposure and underlying holdings require separate review.

The scheduler refreshes discovery after each successful exchange-close research cycle, with no additional historical downloads. The manual Discover button runs on the local Python engine. The public site reads the uploaded results and cannot request work from a sleeping computer. Add & analyse selected saves an audited universe revision, downloads up to 20 selected instruments using the configured history provider, ensures benchmark/FX coverage, and rescans without recording forward signals. Partial provider failure is reported per symbol; no synthetic data is substituted. The next normal research cycle may record eligible new forward signals under the existing rules. Existing signals and backtests remain frozen.

Configuration: `DISCOVERY_ENABLED=true`, `DISCOVERY_LIMIT=250`. The feature uses Yahoo through pinned `yfinance==1.7.0`, requiring no account/key. It uses undocumented Yahoo endpoints through an open-source client, with no guaranteed availability, rate quota, completeness, delay or market-data licence. Application requests are spaced at least two seconds; scheduled refresh is once per exchange session and manual identical requests are cached for 30 minutes. Authentication cookies/cache stay under ignored `data/yahoo-cache`. The Python package licence does not grant redistribution rights to Yahoo/exchange data. Existing publication settings determine whether normalized discovery research is uploaded.

Primary references checked 1 November 2026:
- [yfinance screen: size and query support](https://ranaroussi.github.io/yfinance/reference/api/yfinance.screen.html)
- [EquityQuery: supported exchange and region filters](https://ranaroussi.github.io/yfinance/reference/api/yfinance.EquityQuery.html)
- [ETFQuery](https://ranaroussi.github.io/yfinance/reference/api/yfinance.ETFQuery.html)
- [yfinance data-use notice](https://github.com/ranaroussi/yfinance#download-market-data-from-yahoo-finances-api)

Current constituents and discovery snapshots do not establish point-in-time historical membership or remove survivorship bias.
