# Local engine with five-minute public research uploads

The Windows Python engine remains authoritative for source requests, scans, backtests and forward paper accounting. GitHub Pages is a read-only viewer of allowlisted research. No inbound connection to the home PC, open router port, frontend GitHub credential or cloud Python service is required.

## Enable and run

In the local ignored `.env`:

```dotenv
DATA_MODE=yahoo
ENABLE_NEWS=true
SCHEDULER_ENABLED=true
PUBLISH_ENABLED=true
PUBLISH_REPOSITORY=mohitkapoor2484-ship-it/marketlens
PUBLISH_BRANCH=research-data
PUBLISH_INTERVAL_MINUTES=5
```

The owner has explicitly requested publication for this project. New installations default to publishing disabled. Review the public fields and the source provider's terms before enabling it elsewhere. Public display and redistribution rights are separate from connectivity; the software grants no market-data licence.

Use an existing Git Credential Manager sign-in to GitHub, or set `PUBLISH_GITHUB_TOKEN` locally to a repository-scoped token with Contents write access. Never paste it into the browser or a VITE_ variable. Tokens and the credential helper's output remain in backend memory and are not logged or included in snapshots. GitHub credentials are for publication only; no broker integration exists.

Start normally with `scripts/start.ps1` and keep its window open. Alternatively, `scripts/start-background.ps1` starts a hidden local process; `scripts/stop-background.ps1` stops that recorded process after checking identity, port and active jobs. Logs are in ignored `data/server.stderr.log`. These scripts do not register a Windows boot task. The PC must remain awake and connected.

## What updates and when

- The publisher starts about 20 seconds after startup and then every five minutes. It uploads already computed research and a new heartbeat. This does **not** request five-minute market prices.
- Exchange-aware EOD scans still run after each close plus the configured publication buffer (three hours by default), checked every ten minutes. Successful cycles upload immediately.
- **Refresh & upload now**, in the local dashboard, requests prices/FX and Google News RSS again, evaluates existing paper orders, scans, writes local reports and uploads. Requests retain the two-second source spacing and bounded error handling. Daily observations may legitimately remain unchanged. A full source refresh can take over a minute.
- **Upload saved data**, locally, uploads the cached research without source requests.
- **Check latest upload**, on GitHub Pages, checks for an existing new upload immediately. It cannot remotely start a source refresh or a job on the PC. The browser also checks every minute.
- Three missed five-minute heartbeats produce an **UPLOAD STALE** warning. Source-session, retrieval, scan and upload times are distinct. When the PC is off or the connection fails, the site retains the last verified snapshot and shows its age. Nothing is relabelled as a new market observation.

## Public fields and privacy

The explicit exporter publishes the configured instrument names/symbols, actual/delayed daily research, up to 300 recent chart sessions per instrument, computed indicators, hypothetical setup plans and explanations, source dataset identifiers/timestamps, scanner/strategy parameters and up to ten dated news titles/links per instrument. Feed gaps and unavailable symbols remain visible. No news sentiment is invented.

It excludes the SQLite database, environment, API/GitHub keys, local watchlists, portfolio configuration/balances/holdings/orders, paper signals/ledger/outcomes, saved backtests, reports and detailed job logs. Public paper/backtest/scorecard views explicitly say **local only**; no fabricated zero balance or zero signal count substitutes for private results. Original histories and the authoritative immutable audit stay local.

## Data branch and atomic reads

Uploads use GitHub's Git database API against `research-data`, independent of `main` and the Pages deployment branch. Code branches and private/unrecognised targets are rejected. Updates are fast-forward only; competing publishers fail rather than force overwriting. Run one API process and publisher per account.

Changed research is stored as `data.json` in an immutable Git commit. A separate atomic `latest.json` manifest contains that commit ID, SHA-256 content hash, schema and heartbeat. Unchanged research reuses the existing data commit; only the tiny heartbeat changes. A restart can recover the data reference from the remote manifest. Only a successful reference update advances the local success status in `data/publish-state.json`.

The Pages workflow builds the frontend with the public repository/branch names (not secrets). The browser reads the manifest from `raw.githubusercontent.com`, downloads data by immutable commit ID, checks its checksum and actual-data schema, and swaps only a verified snapshot. Uploads do not trigger a full Pages rebuild: the workflow still runs only for main code pushes. GitHub raw delivery may add caching/propagation delay; this is not streaming or guaranteed low-latency delivery. GitHub storage, API and bandwidth allowances still apply.

Initial unavailability shows a publisher-connection error. After a verified actual snapshot, failures retain that snapshot with a warning; they never switch to fixtures. The separate fixture exporter remains usable explicitly for demo/test builds without `VITE_SNAPSHOT_REPOSITORY`.

## Inspect and recover

Inspect the upload banner or `GET /api/publication`. The local Job History retains upload failures. Credential, connection or ref conflicts leave the previous public manifest intact; the next scheduled attempt retries. Fix the cause, then press **Upload saved data**. Restart the server after `.env` changes. CLI `python -m marketlens publication` inspects status; CLI `publish` uploads, but do not run a CLI writer alongside the API.

New scans/fills remain governed by their original causal rules. Uploading a snapshot creates no signals or fills; historical replays cannot contribute to the genuine-forward milestone.
