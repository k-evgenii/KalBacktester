# 010 — QA gate: rules text must name the right station and settlement source

Track: B (data quality). Extends unit 004. Requested in Track A's weather plan
("the QA gate checks that every event's rules text names its underlying's station").

## Goal
Two new BLOCK checks in the KXHIGHNY QA gate, so that a silent change by Kalshi of the
settlement station or the settlement source stops the load instead of being absorbed.

## Not built
No phrase list or new table: the station is matched by its NWS report code (`station.cli_pil`)
only. Older rules texts that name the station differently (e.g. "Central Park" before
Aug 2026) will BLOCK; handling them is left to the backfill unit that first needs them.
No change to the loader or the schema.

## Files
- `ingest/qa_kxhighny_dump.py` (edit: two new checks, ids 22 and 23)

## Interfaces

**Check 22 (BLOCK) — station named in the rules.** Look up the station the series settles on:
```sql
SELECT s.station_id, s.cli_pil
FROM series se
JOIN underlying u ON u.underlying_id = se.underlying_id
JOIN station s    ON s.station_id = u.station_id
WHERE se.ticker = 'KXHIGHNY';
```
- No row (series not in the database, or its underlying has no station) → fail, detail
  "station for KXHIGHNY unknown: seed the station and link the underlying before loading".
- Otherwise every market's `rules_primary` must contain `"(" + cli_pil + ")"` (e.g. `(CLINYC)`),
  case-sensitive. Each market that does not is listed (ticker and the first 80 characters).

**Check 23 (BLOCK) — settlement source named in the rules.** Every market's `rules_primary`
must contain `"The Weather Company"`, the settlement source the loader writes into
`event.settlement_source` (`ingest/load_kxhighny_dump.py`). A market that names anything else
is listed. Reason: the loader hard-codes that source, so a dump from the earlier NWS period
would otherwise be stored with the wrong source.

Both checks run after check 21, read only `markets.json` and the database (SELECT only), and
are skipped like the others when a check among 1–6 failed. A missing `rules_primary` counts as
a failure of both checks (use `m.get("rules_primary", "")`).

## Agreed tests
(Approved by the user 2026-11-23.) Added to `tests/ingest/check_qa_kxhighny_dump.py`, same pattern
as the existing tests (temporary copy, one change):
16. Untouched copy → PASS, and checks 22 and 23 have status "pass".
17. In one market's `rules_primary` replace `(CLINYC)` with `(CLIMDW)` → BLOCK, check 22, the
    example names that market.
18. In one market's `rules_primary` replace `The Weather Company` with
    `the National Weather Service` → BLOCK, check 23.
19. Test 1's list of expected check ids now includes 22 and 23.

## Test files
- `tests/ingest/check_qa_kxhighny_dump.py` (edit)

## Legacy reference
None.

## Done when
1. `uv run python -m ingest.qa_kxhighny_dump /home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY`
   → PASS, checks 22 and 23 pass.
2. `uv run python -m tests.ingest.check_qa_kxhighny_dump` → all PASS (17 existing lines + 3 new).

If anything here is unclear or looks wrong: stop and report, do not guess.
