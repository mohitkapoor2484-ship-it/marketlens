# MarketLens

A local Windows-first ASX/US end-of-day research terminal. Python/FastAPI, pandas/NumPy, Backtrader event-driven historical research, SQLite/SQLAlchemy and React/TypeScript/Tailwind with Lightweight Charts and equity charts. **Research and paper simulation only. There is no broker integration, live order submission, or broker credential support.**

## GitHub Pages with a local research publisher

Open [the published research dashboard](https://mohitkapoor2484-ship-it.github.io/marketlens/). The local Python engine can upload selected actual research every five minutes; the website checks for new uploads every minute. The local **Refresh & upload now** button also performs an ad hoc source refresh. Portfolios, paper ledgers, saved backtests and secrets stay local. The PC must remain running for new uploads. See [hybrid setup, public fields and freshness](docs/HYBRID.md). The separate fixture exporter remains explicitly available for demonstrations and isolation tests.

## Start on Windows

Prerequisites: Python 3.10–3.12, Node.js 22+ and npm. In PowerShell:

```powershell
git clone https://github.com/mohitkapoor2484-ship-it/marketlens.git
cd marketlens
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Open **http://127.0.0.1:8000**. Setup installs dependencies, builds the frontend and applies database migrations 001–002. The default source is Yahoo Finance daily history with Google News RSS headlines, with no account or API key required. Click Update research or let the enabled close-aware scheduler load observations; unavailable feeds remain unavailable. Set DATA_MODE=demo explicitly for fixed synthetic histories (2017–2025), labelled fixture results and no invented news. A new forward account starts at AUD 10,000 and zero genuine signals.

On a fresh clone, run `scripts/setup.ps1` first; dependencies, the frontend build, local data, reports and credentials are excluded from Git. For an already configured local checkout, `scripts/start.ps1` is sufficient. `Ctrl+C` stops the server. Keep one process using a given SQLite file. Don’t run CLI write operations alongside the server; use the dashboard’s Update Research button instead.

Manual setup/start:

```powershell
Copy-Item .env.example .env
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r backend\requirements.txt
cd frontend
npm ci
npm run build
cd ..
.\.venv\Scripts\python.exe -X utf8 -m uvicorn marketlens.api:app --app-dir backend --host 127.0.0.1 --port 8000 --workers 1
```

Development: run the same API command, then `cd frontend; npm run dev` in a second PowerShell window. Vite uses port 5173 and proxies `/api` to 8000. The production API serves the built dashboard itself. The JSON API contract is `/openapi.json`.

## Edit the stock/ETF list

In the local dashboard, open **Settings → Market universe**. Select a market and Stock/ETF type, enter a ticker (for example BHP, VAS.AX or TSLA), click **Add instrument**, then **Save universe**. Remove unwanted draft rows with the trash button. Changes persist immediately without restarting. Click **Refresh & upload now** to download new instruments and update the public snapshot. The public Settings view shows the list read-only. See [editor validation, paper protections and persistence](docs/UNIVERSE.md).

## Connect actual data — Yahoo Finance + Google News RSS

Read [provider contracts and limitations](docs/PROVIDERS.md). The default `DATA_MODE=yahoo` uses Yahoo's public daily-chart interface and Google News RSS. No EODHD account, Yahoo API key, Google API key, OpenAI key or LLM account is required. These are unofficial/public interfaces with no guaranteed coverage, rate allowance or availability.

1. Copy `.env.example` to `.env` on a new installation. Use `DATA_MODE=yahoo`, `ENABLE_NEWS=true` and `SCHEDULER_ENABLED=true`. Request spacing defaults to two seconds (`YAHOO_REQUEST_INTERVAL_SECONDS=2`). Keep one local server running for unattended updates.
2. Start the app and select **Update research**. A full 14-instrument refresh can take over a minute because prices, FX and headlines are requested sequentially. Data is persisted to the ignored local database. The scheduler checks every ten minutes and refreshes after the corresponding exchange close plus the default three-hour publication buffer.
3. Inspect **Data Health** for each symbol's latest session, source retrieval time, failed jobs and missing sessions. Stock Detail shows the canonical and provider symbols. `BHP.AU` maps to Yahoo `BHP.AX`; `AAPL.US` maps to `AAPL`; US share-class dots map to Yahoo hyphens. Do not silently change `universe.json` to a mixture of provider formats.
4. Yahoo's split-adjusted OHLCV and dividends are restored to an as-traded convention using effective split events. The original source bars, reconstruction factors and any discarded appended quote snapshots are retained in the immutable dataset. Dividend-adjusted close is kept separately and never used as an executable price. This is a reconstruction from revised vendor data, not verified historical point-in-time data. Yahoo does not supply dividend payment dates here; the ledger's disclosed ex-date cash approximation applies.
5. Google News RSS stores only actual titles, publisher names, links, publication and retrieval times. It does not infer sentiment. Successful feeds are cached for 30 minutes inside a running process; scheduled EOD cycles request fresh news. Failed, undated or future-dated items remain unavailable. Article bodies are not copied.
6. Source failures never switch to synthetic data or another provider. Rate limits are shown as failures; no proxy rotation or access-control bypass is used. Market cap is unavailable in the Yahoo chart adapter; enabling a minimum-cap filter excludes unknown values rather than inventing them. Intraday remains disabled.

Yahoo's data is intended for personal research. This local configuration does not confer public redistribution rights. The fixture exporter remains isolated. The optional hybrid publisher exports only explicitly allowlisted public research; it never exports the database, accounts, private signals or credentials. The [Pages guide](docs/GITHUB_PAGES.md) explains the distinction. An always-on private research service requires a separately hosted Python backend with appropriate access controls.

The legacy EODHD adapter remains available only when explicitly selected with `DATA_MODE=eodhd` and a valid key/entitlement. Its quotas and charges are documented in [PROVIDERS.md](docs/PROVIDERS.md). It is never a fallback from Yahoo.

## Use the dashboard

- **Overview / Scanner / Ranked Opportunities:** scan cached data; inspect filters and the score component sum. Shortlists contain at most 10 qualifying stocks, possibly none. The source currency remains AUD or USD; portfolio P/L is AUD.
- **Stock Detail:** pan/zoom daily candles, enable SMA20/50/200 or EMA20 overlays, inspect RSI/MACD/ATR/volatility, hypothetical trigger/stop/target and the saved evidence.
- **News:** actual provider titles, links, publication and retrieval times; articles older than 72 hours are labelled stale. No sentiment is inferred. Demo contains no pretend news.
- **Watchlist:** save/remove configured instruments. Lists survive restarts.
- **Backtesting Lab:** choose a strategy and ≥250-session range leaving ≥200 earlier warmup bars. The fixed fixture supports e.g. 2020-01-02 through 2025-12-31. Train/validation/test use a 60/20/20 chronological split, separate balances, and pre-period warmup. Optional training-only sensitivity varies stop ATR; three expanding walk-forward windows choose parameters using preceding training results only. Repeated holdout viewing invalidates any untouched claim. Saved runs include costs, benchmark, same-stock buy-and-hold, equity/drawdown, trade CSV, full JSON, input hashes, source retrieval times and code hash. **Verify rerun** pins the original datasets/rules and checks the result hash. It refuses exact reruns under changed code.
- **Paper Trading:** evaluate subsequent eligible bars; inspect orders, holdings, marked equity, original signal snapshots and hash-chained events. Only fresh real data can originate forward signals. A signal’s chosen strategy must match the account’s frozen strategy. The account never fills a bar whose opening timestamp precedes signal recording. **Run fixture replay** creates an isolated historical account; none of its signals count as forward tests.
- **Strategy Scorecard:** closed-trade sample sizes, descriptive Wilson win-rate intervals, expectancy and failures by strategy/market, beside distinct historical evidence. **100 evaluable forward signals is a monitoring milestone, not proof of statistical significance.**
- **Settings:** create research-rule revisions, inspect provider/key presence (never key value), quotas and universe. Existing paper signals/account and historical runs retain their frozen assumptions. To configure an initial forward balance/risk/cost/strategy before first startup, set `INITIAL_RULES_PATH` to a JSON document with the same `rules` shape returned by `/api/settings`. A new cohort requires a new database URL; retain the old database for audit. Settings edits affect subsequent scans/backtests, not an already-created forward account.

## Scheduling and reports

The example configuration enables `SCHEDULER_ENABLED=true`; restart and keep the local server running. A single APScheduler worker checks every 10 minutes for each exchange’s latest closed session plus the publication buffer (`PROVIDER_LAG_MINUTES=180`). It catches up missed closes after restart, respects exchange holidays and Melbourne DST, evaluates existing paper signals before recording new ones, and writes daily reports. Failed scheduled cycles retry at most three times, at least 30 minutes apart. HTTP transport/transient provider failures have at most three bounded rate-limited attempts. Inspect failed jobs in Data Health; correct the cause and use Update Research for a manual retry. Saturday/Sunday checks produce one weekly summary per ISO week. Sleeping/offline computers cannot run jobs; the next launch catches up the latest eligible close, never retroactively creates missed forward signals.

When the server is stopped, manual commands are:

```powershell
.\scripts\research.ps1 -Command cycle
.\scripts\research.ps1 -Command health
.\scripts\research.ps1 -Command report
.\scripts\research.ps1 -Command weekly
```

Outputs: `reports/daily-YYYY-MM-DD.md` and `.json`, and `reports/weekly-YYYY-Www.md` and `.json` in Melbourne dates. They can also be downloaded from Overview or `/api/reports`. Daily files refresh atomically; immutable source scans/runs/signals remain in SQLite. The database is `data/marketlens.db` by default, with WAL persistence. Back up the stopped application’s database and reports. Don’t use a synchronizing OneDrive folder for concurrent access from multiple machines; for ongoing use set `DATABASE_URL` to a local nonsynchronized path.

## Verification and deployment

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest backend\tests -q
cd frontend
npm run build
```

See [validation results](docs/VALIDATION.md), [architecture](docs/ARCHITECTURE.md), [execution assumptions](docs/ASSUMPTIONS.md) and [operations / PostgreSQL migration](docs/OPERATIONS.md). `backend/resolved-requirements.txt` captures the installed transitive versions; npm uses `package-lock.json`.

Optional Docker (Docker Desktop required): build the frontend first, copy `.env.example` to `.env`, then `docker compose -f compose.yml up --build`. Only loopback port 8000 is published. Docker startup was not verified unless stated in VALIDATION.md. Future hosted use needs authentication, TLS, PostgreSQL worker coordination and a redistribution-appropriate data licence; this local build deliberately trusts only localhost hosts/origins.

Actual provider coverage, historical vintages, historical membership/delistings and real forward outcomes cannot be established using fixtures. Yahoo and RSS connectivity are checked separately from fixture tests; availability can change. The platform does not claim real-time coverage, guaranteed accuracy, profitability, or a verified profitable strategy. At delivery, the 100-forward-signal milestone remains unfulfilled and must accrue over future eligible sessions; it is never synthesized during setup.
