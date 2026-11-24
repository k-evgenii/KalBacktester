# C03 — Portfolio integration: check a run against the engine

Track: C accounting. Third unit of the overnight queue (PLAYBOOK section 7: "C03 integration").
STATUS: approved (overnight playbook: specs inside the queue count as user-approved).

## Goal
One command that checks a run recorded by the matching engine against the portfolio's own
recomputation: every stored fee equals `kalshi_fee`, every end-of-day position and average
cost equals the engine's `position_snapshot`, and every settlement closes the position with
the right payout. It prints one line per check and exits 0 on PASS, 1 on FAIL.

## Not built
No marks, equity or capital at risk (later C units). No UI. No writing to any table: the
script only reads. `db/schema.sql` is not changed. Applying `db/portfolio.sql` to the live
database is Track A's job (a migration, requested with a NEED message by the lead); this unit
never applies it there.

## Files
- `db/portfolio.sql` (edit: replace the body of `portfolio_calendar`, append two views, add
  their drops at the top of the drop block, before every other drop; nothing else changes)
- `portfolio/__init__.py` (new, empty)
- `portfolio/check_run.py` (new)

## Interfaces

**Calendar rule (changed).** `portfolio_calendar` now runs to the latest of: `run.date_end`,
the last event day, and the `close_time` date of every market whose position is still open
after the run's last event in it. Reason: the engine keeps snapshotting a held, unsettled
position up to the market's close day, and a held position is part of the book every day.

**New views** (each view and column gets a `COMMENT ON`, C01 style):

| View | One row is | Empty means |
|---|---|---|
| `portfolio_snapshot_check` | a run, day and market where `portfolio_position_daily` and `position_snapshot` disagree: a row on one side only, a different `net_units`, or an average cost that differs by more than 0.000001 | the engine's snapshots equal the portfolio |
| `portfolio_settlement_check` | a settlement that leaves units in the market (`units_left <> 0`) or whose payout is not the units on the winning side | every settlement closes its position correctly |

Columns: `portfolio_snapshot_check (run_id, day, market_ticker, portfolio_net, engine_net,
portfolio_avg_cost, engine_avg_cost)`; `portfolio_settlement_check (run_id, market_ticker,
settled_at, settled_units, units_left, payout, expected_payout)`.

**Script.** `uv run python -m portfolio.check_run <run_id> [--db NAME]` (default database
`kalbt`, connection from `db.connect.connect(dbname)`).

```python
def check_run(conn, run_id: int) -> list[tuple[str, bool, list[tuple]]]
```
- Returns three entries, in this order: `("fees", ok, rows)`, `("snapshots", ok, rows)`,
  `("settlements", ok, rows)`, where `rows` are the rows of `fee_check`,
  `portfolio_snapshot_check`, `portfolio_settlement_check` for that `run_id`, ordered by their
  first three columns, and `ok = not rows`.
- Raises `ValueError(f"unknown run_id {run_id}")` when the run is not in `run`.
- Raises `RuntimeError("db/portfolio.sql is not applied to database <name>")` when the view
  `portfolio_snapshot_check` does not exist there (check `to_regclass('portfolio_snapshot_check')`).
- SELECT only; it never commits.

`main()` prints, for each entry: `[PASS] fees` or `[FAIL] fees (n rows)` followed by up to 10
of the rows, one per line, indented by two spaces; then `PASS` or `FAIL` on the last line.
Exit code 0 when all three pass, 1 otherwise; an exception prints its message and exits 2.

## Hard parts

Use this SQL as written (the lead ran it on live run 31 inside a rolled-back transaction: all
three checks empty).

New `portfolio_calendar` (replaces the C02 body):

```sql
CREATE VIEW portfolio_calendar AS
WITH last_state AS (
    -- the last ledger row of each market of each run
    SELECT DISTINCT ON (run_id, market_ticker) run_id, market_ticker, ts, net_after
    FROM portfolio_ledger
    ORDER BY run_id, market_ticker, ts DESC, kind DESC, seq DESC
),
bounds AS (
    SELECT l.run_id,
           min(l.ts)::date AS first_day,
           max(l.ts)::date AS last_day,
           (SELECT max(k.close_time)::date
            FROM last_state s JOIN market k ON k.ticker = s.market_ticker
            WHERE s.run_id = l.run_id AND s.net_after <> 0) AS last_open_close_day
    FROM portfolio_ledger l
    GROUP BY l.run_id
)
SELECT r.run_id, d::date AS day
FROM run r
LEFT JOIN bounds b ON b.run_id = r.run_id
CROSS JOIN LATERAL generate_series(
    least(r.date_start, b.first_day)::timestamp,
    greatest(r.date_end, b.last_day, b.last_open_close_day)::timestamp,
    interval '1 day'
) AS d;
```

The two check views:

```sql
CREATE VIEW portfolio_snapshot_check AS
SELECT coalesce(p.run_id, s.run_id)               AS run_id,
       coalesce(p.day, s.as_of_date)              AS day,
       coalesce(p.market_ticker, s.market_ticker) AS market_ticker,
       p.net_units                                AS portfolio_net,
       s.net_units                                AS engine_net,
       p.cost_basis / abs(p.net_units)            AS portfolio_avg_cost,
       s.avg_cost                                 AS engine_avg_cost
FROM portfolio_position_daily p
FULL JOIN position_snapshot s
       ON s.run_id = p.run_id AND s.as_of_date = p.day AND s.market_ticker = p.market_ticker
WHERE p.run_id IS NULL
   OR s.run_id IS NULL
   OR p.net_units <> s.net_units
   OR abs(p.cost_basis / abs(p.net_units) - s.avg_cost) > 0.000001;

CREATE VIEW portfolio_settlement_check AS
SELECT run_id, market_ticker, ts AS settled_at,
       -delta    AS settled_units,
       net_after AS units_left,
       cash_flow AS payout,
       CASE WHEN yes_price = 1 THEN greatest(-delta, 0) ELSE greatest(delta, 0) END AS expected_payout
FROM portfolio_ledger
WHERE kind = 'settlement'
  AND (net_after <> 0
       OR cash_flow <> CASE WHEN yes_price = 1 THEN greatest(-delta, 0) ELSE greatest(delta, 0) END);
```

Drops to add at the top of the drop block (before the C02 drops):
`DROP VIEW IF EXISTS portfolio_settlement_check;` and
`DROP VIEW IF EXISTS portfolio_snapshot_check;`.

## Agreed tests

New file `tests/portfolio/check_check_run.py`, same method as `tests/recon/check_check_run.py`:
throwaway database `kalbt_portfolio_run_test` (dropped first if left over, dropped at the end
even on failure), `db/schema.sql` then `db/portfolio.sql`, `[PASS]/[FAIL]` lines, exit 1 on
any failure. Reuse the recon fixture by import, without editing it:
`from tests.recon.check_check_run import load_fixture, clean_run, tamper, at, intent, M1, M2, M3`.
If that import fails, stop and report. Engine runs are made with
`engine.match_intents.match_intents(conn, strategy, params, intents)`.

1. `clean_run(conn)`: `check_run` returns three entries, all `ok`, all with no rows.
2. Held unsettled position: a new run with the single intent
   `intent(at(6, 1, 12, 0), M3, "yes", "buy", 3)`. The engine writes snapshots on 2026-06-01
   and 2026-06-02 (M3 closes 06-02 06:00 UTC); `check_run` is all PASS, and
   `portfolio_calendar` for that run has exactly the days 2026-06-01 and 2026-06-02.
3. A snapshot `net_units` changed (fresh `clean_run`, `tamper` on `position_snapshot`, M1 on
   2026-06-01 set to 9): `snapshots` FAIL with exactly one row, `portfolio_net` 10,
   `engine_net` 9; `fees` and `settlements` PASS.
4. A snapshot `avg_cost` changed by 0.01 (fresh run, M2 on 2026-06-01): `snapshots` FAIL, one row.
5. A snapshot row deleted (fresh run, M3 on 2026-06-02): `snapshots` FAIL, one row with
   `engine_net` null and `portfolio_net` 3.
6. A fee changed by one cent (fresh run, the first M1 fill): `fees` FAIL, one row with
   `expected_fee` 0.09; the other two PASS.
7. A settlement `net_units` changed (fresh run, M1 set to 9): `settlements` FAIL, one row with
   `settled_units` 9 and `units_left` 1; `snapshots` also FAIL with exactly one row (2026-06-02,
   M1, `portfolio_net` 1, `engine_net` null), because the portfolio now sees one unit still
   held; `fees` PASS.
8. A settlement `payout` changed (fresh run, M1 payout set to 9): `settlements` FAIL, one row,
   `payout` 9, `expected_payout` 10.
9. Unknown run id 999999 raises `ValueError`; a database without `db/portfolio.sql` (a second
   throwaway database `kalbt_portfolio_run_test_bare` with only `db/schema.sql`) raises
   `RuntimeError`. Drop it at the end.
10. The command line: `uv run python -m portfolio.check_run <clean run id> --db
    kalbt_portfolio_run_test` exits 0 and its last line is `PASS`; on the tampered run of
    test 3 it exits 1 and its last line is `FAIL`.
11. The live database `kalbt` has the same row counts in `run`, `order_intent`,
    `order_outcome`, `settlement`, `position_snapshot` before and after, and no
    `portfolio_snapshot_check` view.

Changed agreed test of C02 (calendar rule): in `tests/db/check_portfolio_daily.py`, test 9's run
keeps its NYC and CHI positions open, and their markets close on 2026-06-30, so its
`portfolio_daily` and `portfolio_daily_underlying` rows now run from 2026-06-01 to 2026-06-30;
every day from 06-04 on repeats the 06-03 values. Update the expected rows of tests 9a and 9b
for that. The same rule extends the EXTRA checks in that file whose runs leave positions open
(their markets also close on 06-30): extend only their expected rows to 06-30, each new day
repeating the last day's end-of-day values, and change nothing else in them (same inputs,
same values on the days they already had). The 11 agreed C02 tests other than 9 must pass
unchanged. (Corrected after QA round 1.) `tests/db/check_portfolio.py`
must pass unchanged.

## Test files
- `tests/portfolio/check_check_run.py` (new)
- `tests/db/check_portfolio_daily.py` (edit: expected rows of tests 9a and 9b only)

## Legacy reference
- `recon/check_run.py` (Track B's reconciliation, same output style: one line per check).
- `engine/match_intents.py` `snapshot_rows`, `position_states` (the engine's snapshot rules).

## Done when
- `PYTHONPATH=. uv run python tests/portfolio/check_check_run.py` exits 0, only `[PASS]` lines.
- `PYTHONPATH=. uv run python tests/db/check_portfolio_daily.py` and
  `tests/db/check_portfolio.py` exit 0.
- `uvx ruff check portfolio/ tests/portfolio/ tests/db/check_portfolio.py tests/db/check_portfolio_daily.py`
  is clean (`tests/db/check_migrate.py` is Track A's file and out of scope).

If anything here is unclear or looks wrong: stop and report, do not guess.
