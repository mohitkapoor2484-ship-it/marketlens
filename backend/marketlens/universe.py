"""Versioned scanner membership; historical and paper evidence stay immutable."""
from .db import PaperAccount, UniverseRevision
from .domain import Instrument, digest, stamp

CORE_BENCHMARKS = {"STW.AU": "ASX", "SPY.US": "US"}


class UniverseConflict(ValueError):
    pass


def validate_universe(items):
    instruments = [Instrument.model_validate(item) for item in items]
    if not 3 <= len(instruments) <= 500:
        raise ValueError("Use 1–498 stocks/ETFs plus the two required portfolio benchmarks (500 instruments maximum)")
    by_symbol = {i.symbol: i for i in instruments}
    if len(by_symbol) != len(instruments):
        raise ValueError("Duplicate tickers are not allowed; edit the existing instrument")
    if not any(i.role in {"stock", "etf"} for i in instruments):
        raise ValueError("Keep at least one stock or ETF to scan")
    for symbol, market in CORE_BENCHMARKS.items():
        if symbol not in by_symbol or by_symbol[symbol].market != market or by_symbol[symbol].role != "benchmark":
            raise ValueError(f"{symbol} must remain a benchmark for the existing forward portfolio comparison")
    for instrument in instruments:
        benchmark = by_symbol.get(instrument.benchmark)
        if not benchmark or benchmark.role != "benchmark" or benchmark.market != instrument.market:
            raise ValueError(f"{instrument.symbol}: select an included benchmark from the same market")
        if instrument.role == "benchmark" and instrument.benchmark != instrument.symbol:
            raise ValueError("Benchmark instruments must reference themselves")
    return instruments


def load_universe(service):
    with service.db.session() as session:
        revision = session.query(UniverseRevision).order_by(UniverseRevision.id.desc()).first()
        if revision is None:
            payload = [i.model_dump() for i in service.universe]
            revision = UniverseRevision(created_at=stamp(), payload=payload, hash=digest(payload))
            session.add(revision)
            session.flush()
        service.universe = validate_universe(revision.payload)
        service.universe_revision = revision.id


def protected_symbols(session):
    reasons = {symbol: "Required forward portfolio benchmark" for symbol in CORE_BENCHMARKS}
    account = session.get(PaperAccount, "forward")
    if account:
        state = account.state
        active = {p["symbol"] for p in state.get("positions", {}).values()}
        active.update(o["symbol"] for o in state.get("orders", []) if o["status"] in {"pending", "filled", "partially_filled"})
        active.update(r["symbol"] for r in state.get("receivables", []))
        reasons.update({symbol: "Used by an active paper order, position or dividend receivable" for symbol in active})
    return reasons


def universe_view(service):
    with service.db.session() as session:
        revision = session.get(UniverseRevision, service.universe_revision)
        protected = protected_symbols(session)
    return {"items": [i.model_dump() for i in service.universe], "revision": service.universe_revision,
            "saved_at": revision.created_at, "hash": revision.hash, "protected": protected, "maximum": 500}


def save_universe(service, items, expected_revision):
    instruments = validate_universe(items)
    payload = [i.model_dump() for i in instruments]
    with service.db.session() as session:
        latest = session.query(UniverseRevision).order_by(UniverseRevision.id.desc()).first()
        if latest.id != expected_revision:
            raise UniverseConflict("The universe changed in another session. Reload the saved list before saving again")
        protected = protected_symbols(session)
        removed = {i["symbol"] for i in latest.payload} - {i.symbol for i in instruments}
        blocked = sorted(removed & protected.keys())
        if blocked:
            raise ValueError("Cannot remove instruments with active paper obligations: " + ", ".join(blocked))
        content_hash = digest(payload)
        if latest.hash == content_hash:
            identifier = latest.id
            changed = False
        else:
            revision = UniverseRevision(created_at=stamp(), payload=payload, hash=content_hash)
            session.add(revision)
            session.flush()
            identifier, changed = revision.id, True
    service.universe, service.universe_revision = instruments, identifier
    result = universe_view(service)
    if changed:
        try:
            service.scan(record_forward=False)
        except Exception:
            result["warning"] = "Universe saved. Cached rescan failed; refresh research before publishing"
    return result
