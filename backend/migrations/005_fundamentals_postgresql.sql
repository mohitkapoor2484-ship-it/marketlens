CREATE TABLE IF NOT EXISTS fundamental_snapshots (
    id VARCHAR PRIMARY KEY, symbol VARCHAR NOT NULL, fetched_at VARCHAR NOT NULL,
    status VARCHAR NOT NULL, payload JSONB NOT NULL, hash VARCHAR NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_fundamental_snapshots_symbol ON fundamental_snapshots(symbol);
DROP TRIGGER IF EXISTS immutable_audit ON fundamental_snapshots;
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON fundamental_snapshots
FOR EACH ROW EXECUTE FUNCTION marketlens_reject_audit_mutation();
INSERT INTO schema_migrations(version, applied_at) VALUES (5, CURRENT_TIMESTAMP::text)
ON CONFLICT (version) DO NOTHING;
