# Edit the stock and ETF universe from the dashboard

1. Open the local dashboard at http://127.0.0.1:8000 and choose **Settings**.
2. In **Market universe**, select ASX or US, choose Stock or ETF, enter the ticker and optionally a display name. The default benchmark is STW for ASX and SPY for US. Click **Add instrument**.
3. Review the draft table. Change display names, stock/ETF classification or the same-market benchmark as needed. The trash button removes an instrument from the draft. **Reload saved list** discards unsaved changes.
4. Click **Save universe**. The new list is active immediately and persists through restarts; no JSON editing or server restart is required. Saving recomputes the scan using cached observations, without creating forward signals or fetching prices.
5. Click **Refresh & upload now** to download newly added instruments, evaluate the paper engine, rescan and publish. The next successful five-minute upload also publishes the updated list and any unavailable-data states. Larger lists take longer and remain subject to source availability/rate limits.

Examples: ASX `BHP`, `VAS`, `VAS.AX` and `VAS.AU` are accepted by the add form; US `TSLA`, `TSLA.US` or `BRK.B` are accepted. The stored canonical symbols are BHP.AU, VAS.AU, TSLA.US and BRK.B.US. The provider adapter handles .AX and share-class hyphens. Choosing a market does not verify a listing: unknown symbols remain unavailable when the provider is queried; no prices are fabricated.

The GitHub Pages **Settings** view displays the published universe read-only. It cannot change the local database or command the PC remotely. Use the local frontend to make edits. GitHub credentials never enter this editor.

## Safeguards and persistence

- Duplicate symbols, incompatible suffixes, blank names, missing or cross-market benchmarks, and more than 500 total instruments are rejected. At least one stock/ETF must remain.
- STW.AU and SPY.US remain benchmark-only for the current forward portfolio comparison. Additional benchmark-only instruments can be added. Benchmarks reference themselves and can be selected only by instruments in the same market.
- Instruments needed by pending/partially filled orders, open positions or dividend receivables cannot be removed. Resolve or cancel the applicable paper obligation first, then reload the list. Referenced benchmark removal is also blocked.
- Every changed list creates an append-only SQLite universe revision. Optimistic revision checking rejects stale saves from another editor session. No-op saves do not create spurious revisions. Cached scans record their universe revision, and publication rejects an outdated membership/scan combination.
- Source datasets, original signals, fills, audit events and saved backtests remain immutable. Removing a symbol from future scans does not delete its history or prevent a frozen saved run from rerunning under its original compatible code revision. Universe changes are not proof of historical point-in-time membership.
- ETF entries use the existing six EOD setup rules and cost model. This does not implement the older ETF contribution-timing or weekly rotation strategies.

Migration **002** adds universe_revisions and SQLite audit guards automatically at startup. The initial revision is seeded from universe.json (or UNIVERSE_PATH). Once the database has a revision, frontend-saved revisions are authoritative; editing the seed file will not overwrite them. The checked-in universe.json remains an installation default and is not changed by the UI. Include the database in normal backups. The reviewed future PostgreSQL path adds backend/migrations/002_universe_postgresql.sql after migration 001; it has not been exercised on PostgreSQL here.
