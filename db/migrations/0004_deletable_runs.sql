-- 0004: saved backtest runs may be deleted, never updated (user decision 2026-11-24, spec A024).
-- candle, market and the weather tables keep every guard.
DROP TRIGGER IF EXISTS order_intent_no_update_delete ON order_intent;
DROP TRIGGER IF EXISTS order_intent_no_truncate ON order_intent;
CREATE OR REPLACE TRIGGER order_intent_no_update BEFORE UPDATE ON order_intent
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

DROP TRIGGER IF EXISTS order_outcome_no_update_delete ON order_outcome;
DROP TRIGGER IF EXISTS order_outcome_no_truncate ON order_outcome;
CREATE OR REPLACE TRIGGER order_outcome_no_update BEFORE UPDATE ON order_outcome
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

DROP TRIGGER IF EXISTS settlement_no_update_delete ON settlement;
DROP TRIGGER IF EXISTS settlement_no_truncate ON settlement;
CREATE OR REPLACE TRIGGER settlement_no_update BEFORE UPDATE ON settlement
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

DROP TRIGGER IF EXISTS position_snapshot_no_update_delete ON position_snapshot;
DROP TRIGGER IF EXISTS position_snapshot_no_truncate ON position_snapshot;
CREATE OR REPLACE TRIGGER position_snapshot_no_update BEFORE UPDATE ON position_snapshot
    FOR EACH ROW EXECUTE FUNCTION forbid_change();

CREATE OR REPLACE FUNCTION run_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    -- compare the whole row except the two columns that may change
    IF (to_jsonb(NEW) - 'status' - 'finished_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'finished_at') THEN
        RAISE EXCEPTION 'only status and finished_at may be updated on run'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS run_no_truncate ON run;
CREATE OR REPLACE TRIGGER run_guard BEFORE UPDATE ON run
    FOR EACH ROW EXECUTE FUNCTION run_guard();
