# B019 — Backtest runs are throwaway unless `--save`

Track: B. The user decided and approved this on 2026-11-24 (plan "Backtest runs: throwaway by
default, saved only on request").

## Goal
A backtest run is a repeatable sandbox: the engine writes the run, the reconciliation checks
it on the same connection, and the run is then **rolled back unless `--save` is given**. So
every run is reconciled, but only saved runs stay in the database as logs. This also fixes
`tests.backtest.check_run_all` on dev. Its tests 02-05 assumed that the newest backup holds no
forecast_favourite run.

## Not built
- No deleting of saved runs. That needs Track A's schema migration first, then a separate small
  unit.
- No real-portfolio snapshot. The start state stays the engine's `starting_cash` param.
- No duplicate-run guard of any kind. Repeating a run is allowed by design.
- No change to the schema, the strategy or recon.

## Files
- `engine/match_intents.py`
- `backtest/run_all.py`
- Tests, for the qa-tester: `tests/engine/check_match_intents.py`,
  `tests/backtest/check_run_all.py`, `tests/backtest/check_b014_dry_run.py`,
  `tests/backtest/check_compare_kalshi.py`

`tests/portfolio/` belongs to Track C and is off limits; Track C adapts its own test.

## Interfaces

### Engine
`match_intents(conn, strategy, params, intents) -> run_id`. The signature is unchanged, but it
**never calls `commit()` or `rollback()`**. The caller owns the transaction.
- Validation errors (unknown market, empty list) still raise `ValueError` before any write.
- If an exception escapes after writes began, the caller must `rollback()`.

Engine CLI: `uv run python -m engine.match_intents INTENTS.csv --strategy NAME [--db NAME] [--save]`.
It prints exactly as now, plus a last line, `saved` or `not saved (rolled back)`. It commits
only with `--save`; otherwise it rolls back after printing.

### run_all
`run_series(conn, series, params, dry, save) -> dict`. This adds `save: bool`.
- Removed: `EXISTING_RUN_SQL`, `find_existing_run` and the status `already_done`.
- `status` is one of `"no_intents"`, `"dry"` or `"ran"`.
- The result dict gains `"saved": bool`, which is true only when it ran and committed.
- All other keys stay as they are. Leave `run_id` as the engine returned it, even when it was
  rolled back.

CLI: `uv run python -m backtest.run_all --db NAME [--dry] [--save] [--series TICKER ...]`.
- `--dry` and `--save` together give an argparse error.
- `print_result` adds `saved` or `not saved` to the line of a ran series.
- The report JSON and the exit code are as now.

## Hard parts (use as written)

```python
# run_series, after intents_for_series and the no_intents / dry returns:
try:
    run_id = match_intents(conn, "forecast_favourite", params, intents)
    report = check_run(conn, run_id)          # same connection: sees the uncommitted rows
except Exception:
    conn.rollback()
    raise
if save:
    conn.commit()
else:
    conn.rollback()
result["saved"] = save
# fill status "ran", run_id, recon_verdict, failed_checks, outcomes, settlements as before
```
In the engine, delete the `try/commit/except rollback` wrapper in `match_intents`, so that it
just returns `write_run(...)`. In the engine `main()`:
1. compute the counts;
2. `conn.commit()` if `--save`, else `conn.rollback()`;
3. print.

## Agreed tests

### `tests/engine/check_match_intents.py`
Keep every existing assert. Add `conn.commit()` in the fixture or tests only where a test truly
needs rows visible on another connection. New tests:
- E1: after `match_intents` on connection A, a second connection B sees no row of that run;
  after `A.commit()`, B sees it.
- E2: after `match_intents` and then `conn.rollback()`, the same connection sees no row of that
  run.
- E3: on a throwaway db, the engine CLI without `--save` prints the run id, the counts and
  `not saved (rolled back)`, and the trading-table counts are unchanged. With `--save`, there is
  exactly one more run.

### `tests/backtest/check_run_all.py`
Its tests run on a restored copy of the newest backup. They must pass **whatever runs or series
that backup already contains**: compute expectations from the copy (`loaded_series`, counts
before and after). Never assume no earlier run exists or that only KXHIGHNY is loaded. Never
hard-code a dump name.
- Keep 01, 01b, 02 (`--dry`), 06, 07, 08 (live unchanged), 10 and 11 as they are.
- Replace 03 with **03 default run**: for each loaded series, status `ran`, recon PASS and
  `saved` false. The trading-table counts on the copy are unchanged afterwards.
- Replace 04 with **04 repeat**: a second default run also writes nothing and also reconciles
  PASS.
- Replace 05 with **05 --save**:
  - exactly one more run per loaded series, counted relative to before;
  - each run reconciles PASS when re-checked with `check_run` on a **new** connection;
  - `run.strategy` is `forecast_favourite` and `run.params` contains the four strategy keys;
  - `saved` is true.
- Add **05b**: a second `--save` makes one more run per series, because there is no duplicate
  guard.
- Remove 09 (extra; it tested `already_done`). Add **09 --dry --save** → argparse error, and
  nothing is written.

### `tests/backtest/check_b014_dry_run.py` and `tests/backtest/check_compare_kalshi.py`
Keep every assert. Add `conn.commit()` after `match_intents` only where the test needs the rows
to persist or be seen from another connection.

## Test files
These are the four test files listed above.

## Legacy reference
none. This changes B012's `engine/match_intents.py` and B017's `backtest/run_all.py`.

## Done when
These all PASS on the worktree:
```
uv run python -m tests.engine.check_match_intents
uv run python -m tests.recon.check_check_run
uv run python -m tests.backtest.check_b014_dry_run
uv run python -m tests.backtest.check_compare_kalshi
uv run python -m tests.backtest.check_forecast_favourite
uv run python -m tests.backtest.check_run_all
uv run python -m tests.ingest.check_qa_weather_dump
```
The reviewer approves. Then the lead does a live default run with
`uv run python -m backtest.run_all --db kalbt`: every series reconciles PASS and the live
trading-table counts are unchanged.

If anything here is unclear or looks wrong: stop and report, do not guess.
