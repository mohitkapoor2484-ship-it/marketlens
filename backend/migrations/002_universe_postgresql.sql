-- Apply after migration 001; SQLite equivalent is automatic in Database.migrate().
CREATE TABLE IF NOT EXISTS universe_revisions (
    id SERIAL PRIMARY KEY,
    created_at VARCHAR NOT NULL,
    payload JSONB NOT NULL,
    hash VARCHAR NOT NULL
);
DROP TRIGGER IF EXISTS immutable_audit ON universe_revisions;
CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON universe_revisions
FOR EACH ROW EXECUTE FUNCTION marketlens_reject_audit_mutation();
INSERT INTO schema_migrations(version, applied_at) VALUES (2, CURRENT_TIMESTAMP::text)
ON CONFLICT (version) DO NOTHING;
