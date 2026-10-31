# MarketLens implementation plan

Build a local, end-of-day research application. Existing workspace prototypes were inspected: reuse their React/Vite/TypeScript/Tailwind/Lightweight Charts conventions; replace implicit synthetic fallbacks, sentiment scores, and volatile storage. Keep the prototypes intact.

1. **Research:** FastAPI, SQLAlchemy/SQLite, validated immutable dataset revisions, replaceable Yahoo Finance (default), legacy EODHD and explicit deterministic fixture adapters, plus Google News RSS, exchange calendars, causal indicators, deterministic setup rules, timestamped news, ranked shortlist, watchlist, data health, and React dashboard.
2. **Historical research:** Backtrader event clock with an explicit AUD execution ledger, next-session stop entries, adverse gaps, stop-first ambiguous bars, cash/risk/participation constraints, costs/FX, immutable run inputs/results, buy-and-hold comparisons, chronological train/validation/test partitions, walk-forward and sensitivity runs. Historical availability and current-universe bias are disclosed; no population-level historical claim without licensed membership data.
3. **Forward simulation:** separately persisted, immutable signals and append-only events; no broker code or credentials. Only fresh real observations can originate forward signals. Use later market sessions, transactional/idempotent settlement and immutable portfolio assumptions. Fixture replays are isolated and never count toward the 100-signal milestone.
4. **Operations:** persistent job attempts, safe retries, close-aware catch-up scheduling, daily/weekly reports, PowerShell scripts, Docker, numbered database migrations, environment-only secrets, comprehensive core/API tests, and production frontend build.

Modules: `providers` → `research` → `strategies`; `execution` is a pure simulation model consumed independently by `backtests` and `paper`. `service` orchestrates database transactions. `jobs` schedules orchestration; `api` exposes validated local operations. Frontend calls only `/api`, never provider keys.

Trust boundaries: ingestion time is server UTC; exchange bars have a separate session and close timestamp; Melbourne display uses IANA timezone/DST. Real data failures remain failures. New observations never rewrite an older dataset, signal, event, or backtest. Auditable updates append rows. Synthetic data is fixed in time and prominently labelled. Forward evidence requires time to elapse and cannot be completed during implementation.

SQLite is the initial target, one API process and one in-process scheduler protected by a process lock and transactional writes. A PostgreSQL deployment requires the migration and worker changes described in `OPERATIONS.md`; do not run multiple schedulers against SQLite.
