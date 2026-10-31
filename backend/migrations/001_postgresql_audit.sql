-- Apply after schema/import, using a migration role; reviewed future deployment path.
CREATE OR REPLACE FUNCTION marketlens_reject_audit_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'immutable audit record'; END; $$;
DO $$
DECLARE table_name text;
BEGIN
  FOREACH table_name IN ARRAY ARRAY['datasets','scans','backtests','signals','paper_events','rule_revisions','evaluations'] LOOP
    EXECUTE format('DROP TRIGGER IF EXISTS immutable_audit ON %I', table_name);
    EXECUTE format('CREATE TRIGGER immutable_audit BEFORE UPDATE OR DELETE ON %I FOR EACH ROW EXECUTE FUNCTION marketlens_reject_audit_mutation()', table_name);
  END LOOP;
END $$;
CREATE TRIGGER immutable_account_config BEFORE UPDATE OF config,kind,created_at ON paper_accounts
FOR EACH ROW EXECUTE FUNCTION marketlens_reject_audit_mutation();
