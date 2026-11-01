# US intraday data: Moomoo quote-only integration

Plan: optional official SDK → bounded local OpenD quote worker → validated completed 5-minute US bars → append-only cache and connection audit → local US Intraday dashboard. Enable collection only after a successful quote test. Keep Yahoo daily history, discovery, Google News RSS, historical tests and the forward EOD paper account separate.

The account's actual permissions are unverified until OpenD is running and signed in. A Moomoo desktop installation alone does not provide the OpenD quote service. No login, password, trading unlock, account lookup or order submission is implemented by MarketLens. Sign in directly in Moomoo OpenD. The adapter only connects to 127.0.0.1, default port 11111, using OpenQuoteContext.

## Setup on Windows

1. In the project directory run `.\.venv\Scripts\python.exe -m pip install -r backend\moomoo-requirements.txt`.
2. Download/install [Moomoo OpenD](https://www.moomoo.com/download/OpenAPI), a separate gateway from the desktop broker app. Open it and sign in yourself. Use the local listening address 127.0.0.1 and port 11111. Keep account credentials out of MarketLens and Git.
3. Restart MarketLens after installing the optional SDK. Open **US Intraday → Test Moomoo connection**. The test requests actual unadjusted 5-minute US candles through the quote API. It never substitutes Yahoo or fixtures for a failed Moomoo request.
4. Choose up to 20 US instruments from the saved universe and enable collection. Enabling performs a fresh data-access test; failure leaves collection disabled. A five-minute background job then refreshes the chosen instruments while MarketLens and OpenD are running. **Refresh US bars** also requests them manually. Pause collection with the toggle and Save.
5. Command-line diagnostic without database writes: `.\.venv\Scripts\python.exe scripts\check_moomoo.py`. Exit 0 means a completed-candle sample was validated; exit 2 means the dependency/access test did not succeed.

Optional `.env`: `MOOMOO_OPEND_PORT=11111`. Connection host is fixed to loopback. SDK calls run in a separate process with a hard timeout, so a stopped gateway cannot indefinitely block the API. The SDK may create its normal diagnostic logs in the Windows user profile; MarketLens does not publish those logs or raw SDK errors. Configuration, probe outcomes and completed bars persist in migration 004. Repeated identical bars are deduplicated; vendor revisions append new observations. Both data and settings records reject hindsight updates/deletes.

## Data and session assumptions

- This is a 5-minute research monitor, not a new intraday trading strategy. Intraday observations never enter the daily scanner, historical backtests or the current forward paper account. No intraday signal count or simulated performance is invented.
- Requests use `Session.ALL`, `SubType.K_5M` and `AuType.NONE`. Actual returned coverage is shown by session: pre-market, regular, after-hours and overnight. Support/entitlement does not guarantee every instrument has trades in every session.
- Provider times are US Eastern. Conversion uses America/New_York and UTC; display uses Australia/Melbourne. Ambiguous/nonexistent local DST timestamps are rejected. Overnight bars are assigned to the following trading day when they start at/after 20:00 ET. Exchange calendars handle holidays and early closes; extended-session eligibility is a documented convention, not a complete venue-specific overnight calendar.
- Only bars whose 5-minute interval ended at least one minute before retrieval are retained. Future, malformed, wrong-symbol and non-session bars are rejected. No missing intervals or zero-volume bars are synthesized. Latest closed bars are marked stale during expected trading windows if over 15 minutes old; the market-closed state preserves the source timestamp and is not a real-time claim.
- Free API access is promotional and account/region/permission dependent. Quote and historical-symbol quotas apply. Subscription success is not proof of consolidated full-market US volume or permission to redistribute data. Provider coverage must be checked before any future volume-based intraday strategy.
- Intraday bars, gateway diagnostics and configuration remain **local only**. Public GitHub Pages shows a local-only marker. No Moomoo prices are uploaded by the five-minute public publisher; separate display/redistribution permission is unresolved.

Primary sources checked 1 November 2026: [permissions and quotas](https://open.moomoo.com/api/overview/api-authority), [subscriptions and sessions](https://openapi.moomoo.com/moomoo-api-doc/en/quote/sub.html), [current candles](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-kl.html), [global quote login status](https://openapi.moomoo.com/moomoo-api-doc/en/quote/get-global-state.html).
