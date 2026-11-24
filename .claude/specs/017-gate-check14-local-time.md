# 017 — Gate check 14 compares market times in the station's local time

Track: A ingestion. Status: APPROVED by the user 2026-11-24 ("start fixing the gate check 14").

## Goal
Check 14 (WARN, "event size and times") assumes fixed UTC offsets that only hold in winter, so it
warns on every summer market. Make it compare opening and closing times in the station's local
time, so it stays quiet on normal markets and keeps flagging genuinely irregular ones.

## Evidence (all 21 loaded New York events, local time America/New_York)
- Open: 10:00 on the day before the event day, every event except two.
- Close: 00:59 on the day after (NWS era, up to 13 Aug 2026) or 01:00 (from 14 Aug 2026).
- Irregular, around the source switch: KXHIGHNY-26AUG14 opened 08-13 13:13:20, KXHIGHNY-26AUG15
  opened 08-14 10:40:18. These must still warn.

## Not built
- No change to severity (stays WARN), id, name, or any other check.

## Files
- `ingest/qa_kxhighny_dump.py` — check 14, plus one small helper shared with check 22

## Hard parts (use as written)
```python
def station_of(conn):
    """(station_id, cli_pil, rules_names, tz_name) of KXHIGHNY's station, or None."""
    with conn.cursor() as cur:
        cur.execute("""SELECT s.station_id, s.cli_pil, s.rules_names, s.tz_name
                       FROM series se
                       JOIN underlying u ON u.underlying_id = se.underlying_id
                       JOIN station s    ON s.station_id = u.station_id
                       WHERE se.ticker = 'KXHIGHNY'""")
        return cur.fetchone()
```
Check 22 uses `station_of` instead of its own query (behaviour unchanged).

Check 14, now `check_14(ctx, conn)` (the steps list passes `conn`, like check 16):
```
station = station_of(conn)
station is None -> fail (WARN), detail "station for KXHIGHNY unknown: cannot check times"
tz = ZoneInfo(station.tz_name)
for each event:
    if len(markets) != 6: problem "<event>: <n> markets, expected 6"          (unchanged)
    day = event_date(event)
    expected_open  = datetime(day - 1 day, 10:00, tzinfo=tz)
    allowed_closes = {datetime(day + 1 day, 00:59, tzinfo=tz), datetime(day + 1 day, 01:00, tzinfo=tz)}
    for each market:
        open_time  != expected_open      -> problem "<ticker>: open_time <t> is not 10:00 local the day before"
        close_time not in allowed_closes -> problem "<ticker>: close_time <t> is not 00:59/01:00 local the day after"
```
Comparisons are between aware datetimes (UTC from the API vs local), so equal instants match.

## Agreed tests
`qa-tester` extends `tests/ingest/check_qa_kxhighny_dump.py` (all existing tests keep passing):
1. November dump: check 14 status `pass`.
2. June dump: check 14 status `pass` (summer time handled).
3. August dump: check 14 status `warn` with exactly 12 problems, all `open_time` problems, on the
   6 markets of KXHIGHNY-26AUG14 and the 6 of KXHIGHNY-26AUG15; no `close_time` problems.
4. November copy with one market's `close_time` moved one hour later → check 14 `warn` naming that
   market's close_time. The verdict is BLOCK, from check 16 (that market is already in the database
   with the original close_time); assert check 16 fails naming the same market's close_time.
5. November copy with one market removed from `markets.json` and its bars and trades files → check
   14 reports the event has 5 markets (other checks may block; only check 14's problem text is
   asserted).

## Test files
- `tests/ingest/check_qa_kxhighny_dump.py`

## Spec correction (lead, 2026-11-24)
Test 4 first required the verdict to stay PASS. All 42 November markets are already loaded, so
changing one market's close_time also trips check 16 (same key, different value), which blocks —
correct gate behaviour. Test 4 now asserts check 14 warns and check 16 blocks on that market.

## Legacy reference
none

## Done when
- `uv run python -m tests.ingest.check_qa_kxhighny_dump` exits 0
- the gate still returns PASS on all three real dumps

If anything here is unclear or looks wrong: stop and report, do not guess.
