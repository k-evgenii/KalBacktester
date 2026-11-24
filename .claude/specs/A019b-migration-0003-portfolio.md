# A019b — Migration 0003: Track C's portfolio views on the live database

Track: A. Status: APPROVED (overnight plan; Track C's `NEED schema` of 2026-11-24, accepted by the lead).

## Goal
Apply `db/portfolio.sql` (Track C's C01–C03: functions and views only, no tables) to the live
database through the migration runner, with a backup first, so the portfolio check and UI screen 2
work on live data.

## Not built
- No change to `db/portfolio.sql` itself (Track C owns it) or to `db/schema.sql`.
- Later portfolio changes are new migrations (0004, …) carrying the full new file; 0003 is never edited.

## Files
- `db/migrations/0003_portfolio_views.sql` (new): a **byte-for-byte copy** of `db/portfolio.sql` at
  commit 1b2f098 (sha256 `0e6b31089a0400366742febee3b422cb270bcd5371709cc64e14cde99b6791db`),
  e.g. `cp db/portfolio.sql db/migrations/0003_portfolio_views.sql`, then verify the sha256.
- `db/README.md`: one line under "Migrations and backups": 0003 = `db/portfolio.sql` (Track C);
  a change to that file needs a new migration with the full file.
- `tests/db/check_migrate.py` (qa-tester): 1c expects exactly the three files 0001, 0002, 0003.

## Real runs (implementer)
1. `uv run python -m db.migrate` on live: it takes a backup and prints `applied 0003_portfolio_views`.
   Report the backup file name. A second run prints `nothing to apply`.
2. `uv run python -m portfolio.check_run 31` on live prints PASS.

## Agreed tests
1. `tests/db/check_migrate.py` 1c: the migrations folder holds exactly `0001_baseline.sql`,
   `0002_us_daily_high_stations.sql`, `0003_portfolio_views.sql`. Every other check unchanged and passing.
2. Lead-run, after the migration: live `schema_migration` holds 0003 with the sha256 above;
   `SELECT count(*) FROM fee_check` and the portfolio views run without error;
   `portfolio.check_run 31` and `32` print PASS.
3. Every other suite still passes (the lead's full list).

## Done when
- 0003 applied live (backup taken), `check_migrate` exits 0, `portfolio.check_run 31` PASS.

If anything here is unclear or looks wrong: stop and report, do not guess.
