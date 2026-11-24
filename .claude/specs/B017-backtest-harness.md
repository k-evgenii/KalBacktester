# B017 — Backtest harness over every loaded city and week

Track: B. Overnight queue item 4 (`.claude/overnight/PLAYBOOK.md` section 7; specs inside the
overnight queues count as user-approved). Track A asked that the list of cities and weeks come
from the database, never from a hard-coded list.

## Goal
One command that, for every city series with loaded data, runs the B016 strategy over all its
loaded event days through the matching engine (B012), reconciles each run (B013), and prints and
saves a summary. No P&L.

## Not built
No P&L, no parameter sweeps, no parallelism, no new strategy, no database schema change.

## Safety (runs are permanent)
The trading tables are append-only, so every run written is kept forever.
- `--db` is **required** (no default), so the live database is only written when named.
- A run is **not repeated**: if a run with the same `strategy`, `params`, `date_start` and
  `date_end` for the same series already exists with status `done`, that series is skipped and
  the existing run id is reported (no new rows).
- `--dry` lists what would run (series, dates, number of intents) and writes nothing.
- The lead takes a database backup before running it against the live database.

## Files
- `backtest/run_all.py` (new)

## Interfaces

```python
def loaded_series(conn) -> list[dict]
```
Series with at least one event that has (a) markets with candles and (b) a `kalshi` row in
`official_daily` for its station and date. One dict per series:
`{"series_ticker", "underlying_id", "station_id", "date_start", "date_end", "n_events"}` where
the dates are the min and max such event dates. Ordered by series ticker. SELECT only.

```python
def run_series(conn, series: dict, params: dict, dry: bool) -> dict
```
1. `intents, infos = strategies.forecast_favourite.intents_for_series(conn, series_ticker,
   date_start, date_end, params)`.
2. Strategy name stored in `run.strategy`: `"forecast_favourite"`. The params passed to the
   engine are the strategy params **flat** (see "Spec correction" below):
   `params = {"decision_time_lst": "09:00", "lead_day": 1, "min_models": 5, "quantity": 10}`;
   `match_intents` merges them with its engine defaults and stores the result in `run.params`.
3. Skip without writing if `dry`, if there are no intents, or if an identical done run exists
   (query below).
4. Otherwise `run_id = engine.match_intents.match_intents(conn, "forecast_favourite", params, intents)`,
   then `report = recon.check_run.check_run(conn, run_id)`.
   From the report: `recon_verdict = report["verdict"]`,
   `failed_checks = [c["id"] for c in report["checks"] if c["status"] == "fail"]`,
   `outcomes = {k: report["counts"][k] for k in ("filled", "rejected", "blocked")}`,
   `settlements = report["counts"]["settlements"]`. When no run is written these are `None`
   (`failed_checks` `[]`), except `already_done`, which reports the existing `run_id`.
   `skipped_days` counts `info["skipped"]` over the infos (reasons that occurred only).
Returns `{"series_ticker", "date_start", "date_end", "n_events", "n_intents",
"skipped_days": {reason: count}, "status": "ran" | "dry" | "no_intents" | "already_done",
"run_id", "recon_verdict", "failed_checks": [ids], "outcomes": {"filled", "rejected", "blocked"},
"settlements"}`.

Existing identical run (hard part, use as written):
```sql
SELECT run_id FROM run
WHERE strategy = 'forecast_favourite' AND status = 'done'
  AND date_start = %(start)s AND date_end = %(end)s
  AND params @> %(strategy_params)s::jsonb
  AND EXISTS (SELECT 1 FROM order_intent i JOIN market m ON m.ticker = i.market_ticker
              JOIN event e ON e.ticker = m.event_ticker
              WHERE i.run_id = run.run_id AND e.series_ticker = %(series)s)
ORDER BY run_id LIMIT 1;
```
`%(strategy_params)s` is `psycopg.types.json.Jsonb(params)` (the four strategy keys).
(`run.date_start` / `date_end` are the UTC dates of the first and last `decided_at`, as the
engine writes them; compute them the same way from the intents before querying.)

Command line:
`uv run python -m backtest.run_all --db NAME [--dry] [--series TICKER ...]`
prints one line per series (series, dates, events, intents, skipped days, status, run id,
recon verdict and failing check ids, filled/rejected/blocked, settlements) and a total line;
writes the list of result dicts as JSON to `backtest/reports/run_all_<UTC timestamp>.json`.
Exit 0 if every run that ran reconciles PASS, else 1.

## Agreed tests
`tests/backtest/check_run_all.py` (plain script, `[PASS]`/`[FAIL]`, exit 1 on failure). Throwaway
database `kalbt_b017_test` restored from the newest dump in `/home/kondr/KalBacktester/backups/db/`
(as in `tests/backtest/check_b014_dry_run.py`), dropped in a `finally`; plus a second throwaway
database built from `db/schema.sql` with a hand-made two-city fixture where needed.
1. `loaded_series` on the restored copy returns KXHIGHNY with its first and last loaded dates and
   event count matching a direct SQL count; a series with markets but no `kalshi` official value
   is not returned (fixture).
2. `--dry` writes nothing (trading-table counts unchanged) and lists each series with its intent
   count.
3. A real run on the restored copy: one run per series returned by `loaded_series`, each
   reconciles PASS; `run.strategy` is `forecast_favourite` and `run.params` contains the four
   strategy keys with their values (`params @> ...`) plus the engine default keys.
4. Running again with the same params writes nothing and reports `already_done` with the first
   run's id.
5. Changing a strategy param (e.g. `quantity` 5) makes a new run.
6. Two-city fixture: both series run; a city whose underlying has no station gives all days
   skipped `no_station` and status `no_intents` (no run row). Such a city can never appear in
   `loaded_series` (it has no station, so no `kalshi` official value), so this test calls
   `run_series` directly with a hand-built series dict, and separately checks that
   `loaded_series` does not return it.
7. `--db` missing → the CLI exits with an argparse error and writes nothing.
8. The live database's trading tables are unchanged by the test script.

## Spec correction (lead, 2026-11-24)
The draft nested the strategy params under `"strategy_params"`. `engine.match_intents.write_run`
stores every non-int param as `str(v)` (engine/match_intents.py:268-269), so a nested dict would
be saved as a Python repr string, and the JSONB equality lookup would never match. Strategy params
are therefore passed flat (no key clashes with the engine's five keys), and the lookup uses JSONB
containment `params @> ...`. The engine is not changed.

## Test files
- `tests/backtest/check_run_all.py` (new)

## Live run (lead only, after review)
Backup (`uv run python -m db.backup dump --reason before-B017-run-all`, Track A's A018 tool), then
`uv run python -m backtest.run_all --db kalbt`; every run must reconcile PASS. Record run ids and
the summary in the progress file and the commit.

## Done when
Tests all PASS; reviewer APPROVE; the live run done with every run reconciling PASS.
