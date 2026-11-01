CREATE TABLE IF NOT EXISTS intraday_config_revisions (
    id SERIAL PRIMARY KEY, created_at VARCHAR NOT NULL, payload JSONB NOT NULL, hash VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS intraday_attempts (
    id SERIAL PRIMARY KEY, created_at VARCHAR NOT NULL, kind VARCHAR NOT NULL,
    status VARCHAR NOT NULL, payload JSONB NOT NULL, hash VARCHAR NOT NULL
);
CREATE TABLE IF NOT EXISTS intraday_bars (
    id SERIAL PRIMARY KEY, symbol VARCHAR NOT NULL, start_at VARCHAR NOT NULL,
    observed_at VARCHAR NOT NULL, provider VARCHAR NOT NULL, payload JSONB NOT NULL, hash VARCHAR NOT NULL,
    UNIQUE(provider, symbol, start_at, hash)
);
CREATE INDEX IF NOT EXISTS ix_intraday_bars_symbol ON intraday_bars(symbol);
CREATE INDEX IF NOT EXISTS ix_intraday_bars_start_at ON intraday_bars(start_at);
DO $$ DECLARE tbl TEXT; BEGIN
    FOREACH tbl IN ARRAY ARRAY['intraday_config_revisions','intraday_attempts','intraday_bars'] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS immutable_audit ON %I', tbl);
        EXECUTE format('CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION marketlens_reject_audit_mutation()', tbl);
    END LOOP;
END $$;
INSERT INTO schema_migrations(version, applied_at) VALUES (4, CURRENT_TIMESTAMP::text)
ON CONFLICT (version) DO NOTHING;
