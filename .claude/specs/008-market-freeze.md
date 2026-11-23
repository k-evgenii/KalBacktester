# 008 — Settled markets are frozen, open markets stay editable

Track: B (database)
(Numbered 008 because Track A's plan uses 005–007 for the weather units.)

## Goal
The database itself refuses any change to a market once it is settled, while an open market's
row may still be updated, including the one update that settles it. A generated boolean
column `is_settled` is the mask, visible in queries.

## Not built
No change to `candle` (stays fully append-only) or to the trading tables (stay append-only).
No loader change. No rule on `event`, `series` or `underlying`.

## Files
- `db/schema.sql` (edit)
- `db/README.md` (edit: rules section and the market row of the tables table)
- `.claude/specs/001-recon-schema.md` (edit: one short note under table 4, `market`)

## Interfaces

In `CREATE TABLE market`, after `settled_at`, add:
```sql
is_settled boolean GENERATED ALWAYS AS (result IS NOT NULL) STORED,
```
with `COMMENT ON COLUMN market.is_settled IS 'true once the market has a result; settled markets can no longer be changed (trigger market_guard)';`

Rule, enforced by trigger `market_guard`:
- UPDATE of a market whose stored row has `is_settled = false`: allowed, except changing
  `ticker` or `event_ticker`.
- UPDATE of a market whose stored row has `is_settled = true`: refused.
- DELETE of any market: refused. TRUNCATE of `market`: refused.
- All refusals use SQLSTATE `restrict_violation`.

## Hard parts (use as written)

Place the function next to `forbid_change()` and `run_guard()`, and the triggers with the
other triggers:

```sql
CREATE FUNCTION market_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DELETE on market is not allowed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF OLD.is_settled THEN
        RAISE EXCEPTION 'market % is settled and can no longer be changed', OLD.ticker
            USING ERRCODE = 'restrict_violation';
    END IF;
    IF NEW.ticker <> OLD.ticker OR NEW.event_ticker <> OLD.event_ticker THEN
        RAISE EXCEPTION 'a market''s ticker and event cannot change'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER market_guard
    BEFORE UPDATE OR DELETE ON market
    FOR EACH ROW EXECUTE FUNCTION market_guard();
CREATE TRIGGER market_no_truncate
    BEFORE TRUNCATE ON market
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
```

Live database (applied once by the lead, not by a worker): the same function and triggers,
plus `ALTER TABLE market ADD COLUMN is_settled boolean GENERATED ALWAYS AS (result IS NOT NULL) STORED;`
and the comment. Additive only; the 42 loaded markets are settled and become frozen.

## Agreed tests
(Approved by the user 2026-11-23.) Added to `tests/db/test_schema.sql`, inside the existing
`BEGIN; ... ROLLBACK;`, using the existing expect-error pattern. Each case uses a new open market
inserted by the test (result and settled_at NULL), under the existing valid event.

1. An open market can be updated (change `close_time`) → succeeds; PASS notice.
2. An open market can be settled (set `result = 'yes'` and `settled_at` in one UPDATE) →
   succeeds and `is_settled` is then true; PASS notice.
3. After 2, `UPDATE` of that market (any column) → `restrict_violation`.
4. Changing an open market's `ticker`, and separately its `event_ticker` → `restrict_violation`.
5. `DELETE` of a market that nothing references → `restrict_violation`; `TRUNCATE market CASCADE`
   → `restrict_violation`.
6. `UPDATE market SET is_settled = true` on an open market → refused (SQLSTATE
   `generated_always`).
7. The 55 existing cases still pass. Existing case "delete of a market referenced by an
   order_intent" must still pass: it now meets `market_guard` first, so change its expected
   condition to `restrict_violation` **or** `foreign_key_violation` (either proves the delete is
   refused) and say so in its comment.

## Test files
- `tests/db/test_schema.sql` (edit)

## Legacy reference
None.

## Done when
(Amended by the lead 2026-11-23: the live database now holds Track A's loaded week, so the
schema tests, which insert their own sample rows and expect empty tables, run only on a
throwaway database built from `db/schema.sql`. Step 2 below is replaced by step 3. Result:
63 PASS, 0 FAIL on the throwaway database.)

Run by the lead:
1. Apply the live-database statements above to `kalbt-recon-db`.
2. `docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < tests/db/test_schema.sql`
   → exit 0, a PASS notice per case, no FAIL or ERROR lines.
3. A fresh scratch container built from `db/schema.sql` (separate project name and volume,
   removed afterwards) passes the same test file, proving schema.sql and the live database agree.
4. On the live database, `UPDATE market SET close_time = close_time WHERE ticker = 'KXHIGHNY-26NOV22-B67.5'`
   is refused.

If anything here is unclear or looks wrong: stop and report, do not guess.
