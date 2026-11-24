# A024 — Migration 0004: saved backtest runs can be deleted

Track: A. Status: APPROVED by the user 2026-11-24 (scope as below). Applied after B019 is merged.

## Why
User decision (Track B's chat, 2026-11-24): backtest runs are throwaway by default. A saved run is a
log for later analysis: it may be deleted, but never edited. The strict append-only rules of spec 001
are meant for a future real-orders database, which does not exist yet.

## Scope (from Track B's NEED)
- **Drop:** the DELETE and TRUNCATE guards on `order_intent`, `order_outcome`, `settlement` and
  `position_snapshot`, and the DELETE branch of `run_guard`, plus `run`'s TRUNCATE guard.
- **Keep:** UPDATE stays forbidden on all of them, and `run` keeps "only status/finished_at may change".
- **Unchanged:** `candle`, `market` (settled freeze) and every weather table stay fully protected.

## Tests that change together with it
`tests/db/test_schema.sql`:
- Test 7, the DELETE and TRUNCATE asserts on the four trading tables (≈ lines 454–499);
- the run guard's "DELETE on run refused" and "TRUNCATE run refused" (≈ lines 530–537).
These become "DELETE/TRUNCATE allowed", with UPDATE still refused.

Unaffected (checked by Track B): recon and portfolio tamper tests (they disable the triggers), the
engine, UI and ingest tests.

## User decisions (2026-11-24)
- Deleting a saved run: one script (`backtest/delete_run.py`, Track B) removes the run and its intents,
  outcomes, settlements and snapshots in one transaction. No ON DELETE CASCADE in the schema.
- Runs 31 and 32 are kept.

## Files
- `db/migrations/0004_deletable_runs.sql` (new)
- `db/schema.sql`: the same end state, written in place (the five trading triggers and run_guard), so a
  fresh build already has it
- `db/README.md`: one line in "Rules": saved runs may be deleted (never updated); data tables never
- `tests/db/test_schema.sql`, `tests/db/check_migrate.py` (qa-tester)

## Hard parts (use as written)
The migration is idempotent (`DROP TRIGGER IF EXISTS`, `CREATE OR REPLACE`), so it also runs cleanly on
a fresh build whose `db/schema.sql` already has the new state:
```sql
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
```
Before applying: `grep -rn "no_update_delete\|no_truncate\|DELETE on run" tests/` and check that every hit is
in the qa-tester's two files or concerns tables this migration does not touch.

## Agreed tests
1. `tests/db/test_schema.sql`:
   - On the four trading tables, DELETE and TRUNCATE are now **accepted**, and UPDATE is still
     refused.
   - On `run`, DELETE (of a run whose child rows are deleted first) and TRUNCATE are accepted;
     changing any column other than status/finished_at is still refused.
   - Candle, market and weather guards are unchanged.
   - Any test 1-style list of trigger names is updated to the new names.
2. `tests/db/check_migrate.py` 1c: the folder holds 0001, 0002, 0003, 0004; on a fresh build `db.migrate`
   applies 0002, 0003 and 0004 without error (0004 is idempotent against the new schema.sql).
3. On a throwaway copy of the live database (restore the newest backup), applying 0004 then deleting a
   copy of run 31's rows in child-first order works, and `UPDATE run SET strategy = 'x'` is still refused.
4. Every other suite still passes.

## Real run (implementer)
`uv run python -m db.migrate` on live (backup first) → `applied 0004_deletable_runs`; second run → nothing.
Runs 31 and 32 stay.

## Done when
- 0004 applied live, every suite green (schema tests on a throwaway database).
