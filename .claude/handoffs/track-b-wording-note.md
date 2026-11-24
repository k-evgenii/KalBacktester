# Note to Track B: Track A is changing the QA gate's station and source checks

From: Track A lead chat, 2026-11-24. Read it at this path from your worktree:
`/home/kondr/KalBacktester/.claude/handoffs/track-b-wording-note.md`.

## Why
Unit 010's checks (rules must contain `(CLINYC)` and `The Weather Company`) are correct from
16 Aug 2026 on but block every older market, so the backfill (Oct 2024 onward) cannot load. The
user approved a fix built in Track A.

## What Track A will change (units 014–016, on `dev`)
- **014:** `station.rules_names text[]` (accepted station phrases, e.g. `(CLINYC)`,
  `Central Park, New York`, `New York City`), appended to `db/schema.sql`; KNYC's names in
  `db/seed_stations.sql`; `tests/db/test_weather_schema.sql` extended.
- **015:** `ingest/qa_kxhighny_dump.py` checks 22 and 23 rewritten (22: rules text contains any
  of the station's `rules_names`; 23: source recognised — "The Weather Company" or "National
  Weather Service" — and consistent with the date, NWS before 2026-08-14, TWC from then);
  `ingest/load_kxhighny_dump.py` writes the source read from the text per event;
  `tests/ingest/check_qa_kxhighny_dump.py` gets new cases. Check ids stay the same.
- **016:** numeric station check in `ingest/load_weather_dump.py`.

## What we ask
- Please **do not edit** `ingest/qa_kxhighny_dump.py`, `tests/ingest/check_qa_kxhighny_dump.py`
  or `ingest/load_kxhighny_dump.py` until unit 015 is merged into `dev`; we will tell the user
  when it is.
- **Unit numbers:** your plan names your next units 011–013; Track A uses 014–016 for this work,
  so there is no clash.
- Your planned optional database-name argument on `db/connect.py` is fine with Track A.
- `event.settlement_source` will hold either `The Weather Company` or `National Weather Service`
  once older weeks are loaded; your `event_outcome` view should not assume a single value.
