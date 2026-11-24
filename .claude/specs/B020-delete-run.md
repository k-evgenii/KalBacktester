# B020 — Delete saved backtest runs

Track: B. The user approved the scope on 2026-11-24, together with Track A's A024 (migration
0004). This unit starts only after 0004 is live on dev.

## Goal
A saved backtest run is a log. This script deletes one or more saved runs and all their rows,
in one transaction: either every listed run goes or none does. Market, candle and weather data
are never touched.

## Not built
- No ON DELETE CASCADE and no schema change; Track A owns 0004.
- No editing of runs.
- No bulk filter (for example "delete all runs of a strategy"). Run ids are given explicitly.
- No confirmation prompt. The required `--db` is the guard, and running it against the live
  database is the user's call.

## Files
- `backtest/delete_run.py` (new)

## Interfaces
```python
def delete_runs(conn, run_ids: list[int]) -> dict[str, int]
```
It deletes the runs and returns the number of rows deleted per table, in delete order:
`{"order_outcome", "order_intent", "settlement", "position_snapshot", "run"}`.
- If any id does not exist, it raises `ValueError` naming the missing ids, before any delete.
- It does not commit. The caller owns the transaction, the same rule as the engine since B019.

CLI: `uv run python -m backtest.delete_run RUN_ID [RUN_ID ...] --db NAME`. `--db` is required.
1. It calls `delete_runs`.
2. On success it commits and prints one line per table (`order_outcome: 31`, ...) and then
   `deleted runs [..]`.
3. On error it rolls back, prints the error, and exits 1.

## Hard parts (use as written)
The order follows the foreign keys: `order_outcome.intent_id` points to `order_intent`, and
`order_intent`, `settlement` and `position_snapshot` each point to `run`.
```python
cur.execute("SELECT run_id FROM run WHERE run_id = ANY(%s)", (run_ids,))
missing = sorted(set(run_ids) - {r[0] for r in cur.fetchall()})
if missing:
    raise ValueError(f"no such run: {missing}")
statements = [
    ("order_outcome", "DELETE FROM order_outcome WHERE intent_id IN "
                      "(SELECT intent_id FROM order_intent WHERE run_id = ANY(%s))"),
    ("order_intent", "DELETE FROM order_intent WHERE run_id = ANY(%s)"),
    ("settlement", "DELETE FROM settlement WHERE run_id = ANY(%s)"),
    ("position_snapshot", "DELETE FROM position_snapshot WHERE run_id = ANY(%s)"),
    ("run", "DELETE FROM run WHERE run_id = ANY(%s)"),
]
counts = {}
for table, sql in statements:
    cur.execute(sql, (run_ids,))
    counts[table] = cur.rowcount
return counts
```

## Agreed tests
`tests/backtest/check_delete_run.py`: a plain script with `[PASS]`/`[FAIL]` lines and exit 1 on
any failure.
- It uses a throwaway database `kalbt_b020_test` built from `db/schema.sql`. Since A024
  (`night/A024`), `db/schema.sql` already allows DELETE and TRUNCATE on the run tables, so no
  migration needs to be applied. Pattern: `tests/db/check_deletable_runs.py`.
- The fixture is the market and candle fixture of `tests/engine/check_match_intents.py`, with
  saved runs made by `match_intents` followed by `commit`. At least one run has a fill, a
  settlement and a snapshot.
- The database is dropped in `finally`.

1. Two saved runs, A and B. Deleting A:
   - removes every row of A from all five tables, and the returned counts equal the rows A had
     before;
   - leaves B's row counts in all five tables unchanged.
2. An unknown run id raises `ValueError`, and nothing is deleted.
3. `[A, unknown]` in one call raises `ValueError`, and A is still fully present: all or nothing.
4. After deleting a run, the portfolio views (`portfolio_ledger`, `portfolio_daily`) have no
   rows for it, if those views exist in the test database. The views come from Track C's
   `db/portfolio.sql` or migration 0003.
5. `candle` and `market` row counts are unchanged by any delete.
6. CLI:
   - without `--db` it gives an argparse error;
   - with a valid id it exits 0, prints the per-table lines and `deleted runs [<id>]`, and the
     run is gone;
   - with an unknown id it exits 1 and nothing is deleted.
7. The live `kalbt` trading-table counts are unchanged by the test script.

## Test files
- `tests/backtest/check_delete_run.py` (new)

## Legacy reference
none

## Done when
`uv run python -m tests.backtest.check_delete_run` all PASS, the other Track B suites stay
green, and the reviewer approves. The script is never run against live `kalbt` in this unit;
runs 31 and 32 are kept.

If anything here is unclear or looks wrong: stop and report, do not guess.
