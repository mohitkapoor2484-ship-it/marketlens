from contextlib import contextmanager
from pathlib import Path
from threading import RLock

from sqlalchemy import JSON, Column, Integer, String, Text, UniqueConstraint, create_engine, event, text
from sqlalchemy.orm import declarative_base, sessionmaker

from .domain import AppRules, canonical, digest, stamp

Base = declarative_base()
write_lock = RLock()


class Dataset(Base):
    __tablename__ = "datasets"
    id = Column(String, primary_key=True)
    symbol = Column(String, index=True, nullable=False)
    mode = Column(String, nullable=False)
    provider = Column(String, nullable=False)
    fetched_at = Column(String, nullable=False)
    content_hash = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)


class Scan(Base):
    __tablename__ = "scans"
    id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False)
    mode = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)


class Backtest(Base):
    __tablename__ = "backtests"
    id = Column(String, primary_key=True)
    created_at = Column(String, nullable=False)
    input_hash = Column(String, nullable=False, index=True)
    payload = Column(JSON, nullable=False)


class Signal(Base):
    __tablename__ = "signals"
    id = Column(String, primary_key=True)
    dedupe = Column(String, nullable=False, unique=True)
    account_id = Column(String, nullable=False, index=True)
    symbol = Column(String, nullable=False)
    recorded_at = Column(String, nullable=False)
    kind = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    hash = Column(String, nullable=False)


class PaperAccount(Base):
    __tablename__ = "paper_accounts"
    id = Column(String, primary_key=True)
    kind = Column(String, nullable=False)
    created_at = Column(String, nullable=False)
    config = Column(JSON, nullable=False)
    state = Column(JSON, nullable=False)


class PaperEvent(Base):
    __tablename__ = "paper_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    account_id = Column(String, nullable=False, index=True)
    key = Column(String, nullable=False, unique=True)
    created_at = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    previous_hash = Column(String, nullable=False)
    hash = Column(String, nullable=False)


class Evaluation(Base):
    __tablename__ = "evaluations"
    id = Column(Integer, primary_key=True)
    account_id = Column(String, nullable=False)
    symbol = Column(String, nullable=False)
    session = Column(String, nullable=False)
    __table_args__ = (UniqueConstraint("account_id", "symbol", "session"),)


class RuleRevision(Base):
    __tablename__ = "rule_revisions"
    id = Column(Integer, primary_key=True)
    created_at = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)


class Job(Base):
    __tablename__ = "jobs"
    id = Column(Integer, primary_key=True)
    key = Column(String, nullable=False, index=True)
    started_at = Column(String, nullable=False)
    finished_at = Column(String)
    status = Column(String, nullable=False)
    detail = Column(Text)


class UniverseRevision(Base):
    __tablename__ = "universe_revisions"
    id = Column(Integer, primary_key=True)
    created_at = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    hash = Column(String, nullable=False)


class Usage(Base):
    __tablename__ = "provider_usage"
    id = Column(Integer, primary_key=True)
    day = Column(String, nullable=False)
    credits = Column(Integer, nullable=False)


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    id = Column(Integer, primary_key=True)
    created_at = Column(String, nullable=False)
    mode = Column(String, nullable=False, index=True)
    market = Column(String, nullable=False)
    request_hash = Column(String, nullable=False, index=True)
    status = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)


class Watch(Base):
    __tablename__ = "watchlist"
    symbol = Column(String, primary_key=True)
    added_at = Column(String, nullable=False)


IMMUTABLE = ["datasets", "scans", "backtests", "signals", "paper_events", "rule_revisions", "evaluations", "universe_revisions", "discovery_runs"]


class Database:
    def __init__(self, url):
        if url.startswith("sqlite:///"):
            path = url.removeprefix("sqlite:///")
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
        opts = {"connect_args": {"check_same_thread": False, "timeout": 30}} if url.startswith("sqlite") else {}
        if url.endswith(":memory:"):
            from sqlalchemy.pool import StaticPool
            opts["poolclass"] = StaticPool
        self.engine = create_engine(url, **opts)
        if url.startswith("sqlite"):
            @event.listens_for(self.engine, "connect")
            def pragmas(conn, _):
                conn.execute("PRAGMA journal_mode=WAL")
                conn.execute("PRAGMA foreign_keys=ON")
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self, initial_rules=None):
        with self.engine.begin() as con:
            con.execute(text("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at VARCHAR NOT NULL)"))
            exists = con.execute(text("SELECT version FROM schema_migrations WHERE version=1")).first()
        if not exists:
            Base.metadata.create_all(self.engine)
            with self.engine.begin() as con:
                con.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (1, :now)"), {"now": stamp()})
        # Migration 002 adds the local universe without rewriting migration 001.
        UniverseRevision.__table__.create(self.engine, checkfirst=True)
        DiscoveryRun.__table__.create(self.engine, checkfirst=True)
        with self.engine.begin() as con:
            if not con.execute(text("SELECT version FROM schema_migrations WHERE version=2")).first():
                con.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (2, :now)"), {"now": stamp()})
            if not con.execute(text("SELECT version FROM schema_migrations WHERE version=3")).first():
                con.execute(text("INSERT INTO schema_migrations(version, applied_at) VALUES (3, :now)"), {"now": stamp()})
            if self.engine.dialect.name == "sqlite":
                for table in IMMUTABLE:
                    for operation in ("UPDATE", "DELETE"):
                        con.execute(text(f"CREATE TRIGGER IF NOT EXISTS immutable_{table}_{operation} BEFORE {operation} ON {table} BEGIN SELECT RAISE(ABORT, 'immutable audit record'); END"))
                con.execute(text("CREATE TRIGGER IF NOT EXISTS account_config_immutable BEFORE UPDATE OF config,kind,created_at ON paper_accounts BEGIN SELECT RAISE(ABORT, 'immutable account assumptions'); END"))
        with self.session() as s:
            if not s.query(RuleRevision).first():
                s.add(RuleRevision(created_at=stamp(), payload=(initial_rules or AppRules()).model_dump()))

    @contextmanager
    def session(self):
        with write_lock, self.sessions.begin() as session:
            yield session


def rules(session):
    return AppRules.model_validate(session.query(RuleRevision).order_by(RuleRevision.id.desc()).first().payload)


def latest_dataset(session, symbol, mode, asof=None):
    q = session.query(Dataset).filter_by(symbol=symbol, mode=mode)
    if asof:
        q = q.filter(Dataset.fetched_at <= asof)
    return q.order_by(Dataset.fetched_at.desc()).first()


def append_event(session, account_id, key, payload):
    if session.query(PaperEvent).filter_by(key=key).first():
        return
    previous = session.query(PaperEvent).filter_by(account_id=account_id).order_by(PaperEvent.id.desc()).first()
    prev = previous.hash if previous else "0" * 64
    created = stamp()
    session.add(PaperEvent(account_id=account_id, key=key, created_at=created, payload=payload,
                           previous_hash=prev, hash=digest({"previous": prev, "key": key, "created_at": created, "payload": payload})))
    session.flush()
