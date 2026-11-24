# A026 — Two ingest checks scoped to the rows they own after the backfill

Track: A. Status: APPROVED (user, 2026-11-24: "fix them first, as a small unit A026").

## Goal
Since the backfill, Chicago holds 13 weeks and a second Miami backup exists, so two checks that
assumed "the only data in the database" now fail although the code is right. Scope them to the rows
the test owns, without weakening what they check.

## Not built
- No production code change. No other check is touched. No new test file.

## Files
None (tests-only unit; see Test files).

## Hard parts (use as written)

`tests/ingest/check_any_series.py`, test 4 and 5c — restrict every KXHIGHCHI query to the A020 week:

```python
# 4a / 4b
cur.execute("SELECT ticker, event_date, underlying_id FROM event WHERE series_ticker = 'KXHIGHCHI' "
            "AND event_date BETWEEN '2026-11-16' AND '2026-11-22' ORDER BY event_date")
# 4c
cur.execute("SELECT m.event_ticker, count(*), count(*) FILTER (WHERE m.result = 'yes') FROM market m "
            "JOIN event e ON e.ticker = m.event_ticker "
            "WHERE e.series_ticker = 'KXHIGHCHI' AND e.event_date BETWEEN '2026-11-16' AND '2026-11-22' "
            "GROUP BY m.event_ticker")
# 5c
cur.execute("SELECT e.event_date, m.strike_type, m.floor_strike, m.cap_strike FROM event e "
            "JOIN market m ON m.event_ticker = e.ticker "
            "WHERE e.series_ticker = 'KXHIGHCHI' AND m.result = 'yes' "
            "AND e.event_date BETWEEN '2026-11-16' AND '2026-11-22'")
```
The assertions (exactly the 7 dates 16..22, all MDW_HIGH and 7 events, dump market counts with exactly
one yes and the same event set as the dump with 7 events, 7 winners in range) stay exactly as they are.
Rename labels only where they say "KXHIGHCHI events" so they keep naming the week (4a already does).
4d may stay as it is (it holds for all Chicago weeks).

`tests/ingest/check_backfill.py`, 3e:
```python
report("3e at least one backup *_before-backfill-KXHIGHMIA.dump",
       len([b for b in backup_files() if b.endswith("_before-backfill-KXHIGHMIA.dump")]) >= 1)
```
Reason (put it in a one-line comment): the full backfill run takes one backup per new week, so more
than one Miami backup is expected.

## Agreed tests
1. `uv run python -m tests.ingest.check_any_series` (or its documented run command): every check PASS,
   exit 0.
2. `uv run python -m tests.ingest.check_backfill`: every check PASS, exit 0.
3. Neither file has any other change (git diff shows only the lines above and the comment).

## Test files
- `tests/ingest/check_any_series.py`
- `tests/ingest/check_backfill.py`

## Legacy reference
none

## Done when
- both commands above exit 0 against the live database `kalbt` (read-only; neither test may write to
  live tables beyond what it already did before this change).

If anything here is unclear or looks wrong: stop and report, do not guess.
