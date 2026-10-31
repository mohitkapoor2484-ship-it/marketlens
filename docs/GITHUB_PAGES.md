# GitHub Pages and the Python research service

The public preview is https://mohitkapoor2484-ship-it.github.io/marketlens/.

GitHub Pages serves static HTML, JavaScript, CSS and generated fixture JSON. It does not run FastAPI, Python background jobs or SQLite. Publishing this repository's root through the legacy Jekyll workflow displays documentation rather than building the React application. The dedicated `pages.yml` workflow builds the frontend with `/marketlens/` as its asset base and deploys `frontend/dist-pages` using GitHub Actions.

The Pages build is an **explicit synthetic fixture preview**. It supports scanner filtering/sorting, stock candlesticks and indicators, inspecting a saved fixture experiment (including sensitivity/walk-forward results), downloading its JSON/CSV, viewing the separately labelled fixture replay, reports, data-health/settings inspection and browser-local watchlists. It never requests `/api` on the Pages host, never uses provider keys, and never counts a replay as forward evidence. New engine jobs and rule edits are disabled in this preview. Forward evidence remains zero.

Every build exports from a newly created in-memory database with an explicitly selected fixture provider, blank API key, disabled news/fundamentals, disabled scheduler and temporary report directory. Existing user databases, accounts, reports and environment-file values are not used for publication. Generated fixture files are ignored by Git and included only in the Pages artifact. An unavailable real API never silently switches to this preview: the `pages` build mode explicitly selects it; ordinary local builds still use FastAPI.

To use the complete research platform locally:

```powershell
git clone https://github.com/mohitkapoor2484-ship-it/marketlens.git
cd marketlens
powershell -ExecutionPolicy Bypass -File .\scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File .\scripts\start.ps1
```

Then open http://127.0.0.1:8000. Use DATA_MODE=yahoo and ENABLE_NEWS=true in the local ignored `.env`; the full engine then uses Yahoo Finance and Google News RSS without a paid account or API key. Enable local schedules as described in README. The public Pages preview intentionally keeps fixture-only publication under the sources’ personal-use terms. No provider key belongs in Pages, JavaScript, GitHub repository variables prefixed `VITE_`, or a published artifact.

For an always-on full hosted service, deploy the Python API, persistent database and scheduler to a Python-capable server with authentication, TLS, appropriate origin/host controls and a suitable market-data licence. The current localhost security policy must not simply be removed to expose an unauthenticated API. This Pages repair does not provision such a server or create actual forward signals.

To rebuild the preview manually on Windows:

```powershell
.\.venv\Scripts\python.exe -X utf8 scripts\export_pages.py
cd frontend
npm ci
npm run build:pages
npm run preview -- --mode pages --port 4173
```

Visit http://127.0.0.1:4173/marketlens/. To return to the normal local API build, run `npm run build`, then start the Python service. The normal build continues to write `frontend/dist`, while the preview writes `frontend/dist-pages`. The default build does not change its `/` base or API behaviour.

In repository Settings → Pages, **Source must be GitHub Actions**, rather than a branch/root folder. Pushes to main then install dependencies, run tests, generate fixture exports, build, upload the Pages artifact and deploy. Check the deployment workflow before concluding that a new version is live. The existing repository and Pages visibility are preserved.
