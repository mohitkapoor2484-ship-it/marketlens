CREATE TABLE IF NOT EXISTS discovery_runs (
    id SERIAL PRIMARY KEY,
    created_at VARCHAR NOT NULL,
    mode VARCHAR NOT NULL,
    market VARCHAR NOT NULL,
    request_hash VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    payload JSONB NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_discovery_runs_request_hash ON discovery_runs(request_hash);
CREATE INDEX IF NOT EXISTS ix_discovery_runs_mode ON discovery_runs(mode);
DROP TRIGGER IF EXISTS immutable_audit ON discovery_runs;
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON discovery_runs
FOR EACH ROW EXECUTE FUNCTION marketlens_reject_audit_mutation();
INSERT INTO schema_migrations(version, applied_at) VALUES (3, CURRENT_TIMESTAMP::text)
ON CONFLICT (version) DO NOTHING;
