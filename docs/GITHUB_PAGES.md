# GitHub Pages research dashboard

The public dashboard is https://mohitkapoor2484-ship-it.github.io/marketlens/.

The production Pages build is a read-only viewer of actual research uploaded by the local Python engine. It reads a manifest from the public research-data branch, downloads data by immutable commit ID and checks its SHA-256 before displaying it. Source-session and retrieval times remain distinct from upload time. No API on the home computer is publicly exposed. See [HYBRID.md](HYBRID.md) for setup, five-minute publishing, public fields and recovery.

The local **Refresh & upload now** button requests sources, evaluates paper orders, scans, writes local reports and uploads. The public **Check latest upload** button reads an existing upload; it cannot remotely command the PC. The website also checks every minute. The PC must remain awake and connected for new uploads. A stale heartbeat is clearly shown, and failures retain the last verified snapshot without falling back to fixtures.

Paper accounts, portfolio settings, original signals, orders, outcomes, saved backtests, reports, local watchlists, environment values and credentials are excluded. Private views say local only. Publication of market observations or headline links requires appropriate source/display rights; this software grants none.

## Code deployment and uploads

Repository Settings -> Pages -> Source must be **GitHub Actions**. Pushes to main run backend tests, generate isolated demonstration fixtures for isolation checks, build the Pages frontend with VITE_SNAPSHOT_REPOSITORY and VITE_SNAPSHOT_BRANCH, run browser-reader/artifact checks and deploy frontend/dist-pages. These public repository names are not credentials. The compiled app selects the actual-research reader; the included test fixtures are not its fallback data source.

Five-minute uploads update only the separate data branch through GitHub's Git database API. They do not rebuild the frontend or change main. The browser checks an atomic manifest and immutable data reference, so it cannot mix a new header with old chart data. GitHub caching/propagation may delay visibility; there is no guaranteed streaming or intraday coverage.

## Explicit fixture demonstration

For an offline/fixture preview, omit VITE_SNAPSHOT_REPOSITORY:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\export_pages.py
cd frontend
npm run build:pages
npm run preview -- --mode pages --port 4173
```

Open http://127.0.0.1:4173/marketlens/. The fixture exporter uses a newly created in-memory demo database, blank key, disabled source requests and scheduling, and temporary reports. It never reads the user's database or publishes actual account data. Fixture results and replays are labelled and never count as forward evidence. Artifact tests continue to enforce that isolation independently of the actual research publisher.

## Full local app

Run scripts/setup.ps1 once, then scripts/start.ps1. Open http://127.0.0.1:8000. The ordinary frontend build writes frontend/dist and uses the local FastAPI /api endpoints. The Pages build writes frontend/dist-pages and has the /marketlens/ base. Do not upload .env, SQLite, data/, reports/ or credentials to either artifact.
