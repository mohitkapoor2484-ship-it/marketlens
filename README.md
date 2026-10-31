# MarketLens

A local Windows-first ASX/US end-of-day research terminal. Python/FastAPI, pandas/NumPy, Backtrader event-driven historical research, SQLite/SQLAlchemy and React/TypeScript/Tailwind with Lightweight Charts and equity charts. **Research and paper simulation only. There is no broker integration, live order submission, or broker credential support.**

## GitHub Pages preview

Open [the interactive Pages preview](https://mohitkapoor2484-ship-it.github.io/marketlens/). It uses explicit synthetic fixture snapshots. Scanner filters, charts, a saved historical experiment, replay inspection and downloads work without a server; new scans, backtests, scheduling and genuine forward paper trading require the Python service below. See [Pages deployment and limitations](docs/GITHUB_PAGES.md).

## Start on Windows

Prerequisites: Python 3.10–3.12, Node.js 22+ and npm. In PowerShell:

```powershell
git clone https://github.com/mohitkapoor2484-ship-it/marketlens.git
cd marketlens
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Open **http://127.0.0.1:8000**. Setup installs dependencies, builds the frontend and applies database migration 001. The initial demo loads fixed deterministic synthetic daily histories (2017–2025). All fixture prices, scanner rankings and replay results are labelled synthetic. News is unavailable, rather than invented. The forward account starts at AUD 10,000 and **zero genuine signals**.

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

## Connect actual data

Read [provider contracts and costs](docs/PROVIDERS.md). The implemented real adapter is **EODHD**, with `.AU` and `.US` symbols and `USDAUD.FOREX`. No alternate provider is silently substituted. You need your own EODHD account/key and entitlements covering the requested exchanges, history, splits/dividends, FX, and optional news/fundamentals. No OpenAI key or LLM account is required.

1. Copy `.env.example` to `.env` if necessary. Set `DATA_MODE=eodhd` and `EODHD_API_KEY=your-key` locally. Never put secrets into React code or source control.
2. Set a daily credit budget and request limit appropriate to your subscription. The default 20-credit sandbox budget intentionally cannot fund a full universe refresh. A base refresh of 14 instruments needs at least **43 credits** (OHLCV + splits + dividends for each, plus FX); news adds 5 per symbol and fundamentals 10 per symbol. Retries also consume credits. A sandbox may not provide enough history for warmup or historical research.
3. Optionally enable `ENABLE_NEWS=true` and `ENABLE_FUNDAMENTALS=true` only with corresponding entitlements and budget. Market-cap filtering excludes unknown caps when enabled. Current fundamentals are never used as historical point-in-time fundamentals.
4. Maintain `universe.json`; restart after editing. The illustrative stock selection is not a complete exchange universe. Benchmarks must also be listed. Historical membership evidence fields are retained, but the app does not claim a historically complete membership dataset.
5. Restart and click **Update research**. Inspect **Data Health** for errors, missing sessions, latest bar dates, FX and retrieval timestamps. Failure never produces synthetic prices in real mode. Real and demo datasets have separate mode partitions.

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

Set `SCHEDULER_ENABLED=true`, restart, and keep the local server running. A single APScheduler worker checks every 10 minutes for each exchange’s latest closed session plus the publication buffer (`PROVIDER_LAG_MINUTES=180`). It catches up missed closes after restart, respects exchange holidays and Melbourne DST, evaluates existing paper signals before recording new ones, and writes daily reports. Failed scheduled cycles retry at most three times, at least 30 minutes apart. HTTP transport/transient provider failures have at most three bounded rate-limited attempts. Inspect failed jobs in Data Health; correct the cause and use Update Research for a manual retry. Saturday/Sunday checks produce one weekly summary per ISO week. Sleeping/offline computers cannot run jobs; the next launch catches up the latest eligible close, never retroactively creates missed forward signals.

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

Actual provider coverage, historical vintages, historical membership/delistings and real forward outcomes cannot be established using fixtures. The platform does not claim real-time coverage, guaranteed accuracy, profitability, or a verified profitable strategy. At delivery, the 100-forward-signal milestone remains unfulfilled and must accrue over future eligible sessions; it is never synthesized during setup.
