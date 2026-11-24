# A023 — Coverage per city and week (query for UI screen 5)

Track: A. Status: APPROVED (overnight plan PLAYBOOK §7; field contract from Track C, 2026-11-24).

## Goal
One read-only function that tells, for every seeded city and week, how much data is loaded and
whether it can be trusted, for Track C's coverage screen and for the morning summary.

## Not built
- No new table, no writes. No backtest coverage (that is Track B's run summary).

## Files
- `query/coverage.py` (new)
- `tests/query/check_coverage.py` (new, qa-tester)
- `tests/ingest/check_backfill.py` (qa-tester): remove agreed test 6, which reruns every other suite
  inside itself (PLAYBOOK §5: no nested reruns; the lead runs every suite at merge time). Nothing else.

## Interfaces
`coverage(conn, start=date(2026, 8, 24), end=date(2026, 11, 22))` → list of dicts, one per city and week,
ordered by underlying_id then week_start:
underlying_id, series_ticker, station_id, week_start (Monday, date), events, markets, bars, nws_highs,
kalshi_highs, numeric_mismatches, gate_warnings (int or None), status ('OK' | 'Check' | 'RED').

`status(events, nws_highs, kalshi_highs, numeric_mismatches)` → 'OK' | 'Check' | 'RED' (pure function).

## Hard parts (use as written)
Weeks shown for a city: every Monday from `start` to `end` (the backfill window, so gaps show) plus
every other Monday in which that city has at least one event (e.g. the New York June week).
```sql
WITH city AS (
  SELECT u.underlying_id, s.ticker AS series_ticker, u.station_id
  FROM underlying u JOIN series s USING (underlying_id) WHERE u.station_id IS NOT NULL),
weeks AS (
  SELECT c.underlying_id, w::date AS week_start
  FROM city c, generate_series(%(start)s::date, %(end)s::date, interval '7 days') w
  UNION
  SELECT e.underlying_id, date_trunc('week', e.event_date)::date FROM event e),
ev AS (
  SELECT e.underlying_id, date_trunc('week', e.event_date)::date AS week_start,
         count(DISTINCT e.ticker) AS events, count(DISTINCT m.ticker) AS markets
  FROM event e LEFT JOIN market m ON m.event_ticker = e.ticker GROUP BY 1, 2),
bars AS (
  SELECT e.underlying_id, date_trunc('week', e.event_date)::date AS week_start, count(*) AS bars
  FROM candle c JOIN market m ON m.ticker = c.market_ticker JOIN event e ON e.ticker = m.event_ticker
  GROUP BY 1, 2),
hi AS (
  SELECT c.underlying_id, date_trunc('week', d.climate_date)::date AS week_start,
         count(*) FILTER (WHERE d.source = 'nws_cli') AS nws_highs,
         count(*) FILTER (WHERE d.source = 'kalshi') AS kalshi_highs
  FROM official_daily d JOIN city c ON c.station_id = d.station_id GROUP BY 1, 2),
mm AS (
  SELECT c.underlying_id, date_trunc('week', k.climate_date)::date AS week_start, count(*) AS numeric_mismatches
  FROM official_daily k JOIN official_daily n
    ON n.station_id = k.station_id AND n.climate_date = k.climate_date AND n.source = 'nws_cli'
  JOIN city c ON c.station_id = k.station_id
  WHERE k.source = 'kalshi' AND k.high_f <> n.high_f GROUP BY 1, 2)
SELECT c.underlying_id, c.series_ticker, c.station_id, w.week_start,
       coalesce(ev.events, 0) AS events, coalesce(ev.markets, 0) AS markets, coalesce(bars.bars, 0) AS bars,
       coalesce(hi.nws_highs, 0) AS nws_highs, coalesce(hi.kalshi_highs, 0) AS kalshi_highs,
       coalesce(mm.numeric_mismatches, 0) AS numeric_mismatches
FROM city c JOIN weeks w USING (underlying_id)
LEFT JOIN ev   USING (underlying_id, week_start)
LEFT JOIN bars USING (underlying_id, week_start)
LEFT JOIN hi   USING (underlying_id, week_start)
LEFT JOIN mm   USING (underlying_id, week_start)
ORDER BY c.underlying_id, w.week_start;
```
`gate_warnings`, added in Python for each row: read
`api_probe/kalshi/{series_ticker}_{week_start}_{week_start + 6 days}/_qa_report.json` (repo root from
`Path(__file__)`); count checks with `severity == "WARN"` and `status == "fail"`; file missing or
unreadable → None.

```python
def status(events, nws_highs, kalshi_highs, numeric_mismatches):
    if events < 7 or numeric_mismatches >= 2:
        return "RED"
    if nws_highs < events or kalshi_highs < events or numeric_mismatches == 1:
        return "Check"
    return "OK"
```
`gate_warnings` is shown but does not change the status (most weeks warn on quote sums; check 3 warns on
every no-trades dump).

## Agreed tests (`tests/query/check_coverage.py`; read-only on live; a backfill may still be running,
so only fixed facts are asserted)
1. `status`: (7,7,7,0) OK; (6,6,6,0) RED; (7,7,7,2) RED; (7,7,7,1) Check; (7,6,7,0) Check; (7,7,6,0) Check;
   (0,0,0,0) RED.
2. NYC_HIGH week 2026-11-16: events 7, markets 42, bars 41158, nws 7, kalshi 7, mismatches 0, status OK,
   gate_warnings None (the November NY dump folder has the default name `KXHIGHNY`).
3. NYC_HIGH week 2026-06-01 is present (outside the window, it has events): events 7, status OK; its
   gate_warnings equals the count read by the test itself from
   `api_probe/kalshi/KXHIGHNY_2026-06-01_2026-06-07/_qa_report.json` (a data fact).
4. MDW_HIGH week 2026-11-16: events 7, kalshi 7, nws 7, mismatches 0, OK.
5. Every seeded city has all 13 window Mondays (2026-08-24 … 2026-11-16) in the result; every
   `week_start` is a Monday; rows are ordered.
6. EWR_HIGH week 2026-08-24: events < 7 → RED (Kalshi has no markets on 24–25 Aug).
7. The function writes nothing (called in a read-only transaction, rolled back).

## Spec correction (lead, after QA round 1)
`tests/ingest/check_backfill.py` tests 1c and 1d hard-coded the dry run's skip set and totals (3 / 309), a
snapshot of live data that goes stale as the backfill loads. Change them to: the three originally loaded
weeks (KXHIGHNY, KXHIGHCHI, KXHIGHMIA 2026-11-16..22) are **among** the skips, every planned line is
`skip` or `todo`, and the totals line equals the counted skip and todo lines (total 312). Update the
module docstring's test list. Nothing else in that file.

## Done when
- `uv run python -m tests.query.check_coverage` exits 0; `tests.ingest.check_backfill` still exits 0.

If anything here is unclear or looks wrong: stop and report, do not guess.
