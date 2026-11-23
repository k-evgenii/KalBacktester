# Note to Track B: weather tables added on `dev` (unit 007)

From: Track A lead chat, 2026-11-23. Read before merging `dev` into `track-b` or committing
your schema and test changes (specs 004 and 008), since we edited some of the same files.

## What changed on `dev`

1. **`db/schema.sql`** — 153 lines appended at the **end of the file** only: tables `station`,
   `official_daily`, `observation_hourly`, `forecast_hourly`, the column
   `underlying.station_id` (nullable FK → `station`), six append-only triggers using your
   `forbid_change()`, and comments. Nothing above line 332 was touched, so your mid-file
   additions (market freeze) should merge cleanly.
2. **`db/seed_stations.sql`** — new: the KNYC station row and `NYC_HIGH → KNYC`.
3. **`db/README.md`** — new section "Weather tables" inserted immediately **before**
   `## Usage`. Your edits near the end of the file should not overlap.
4. **`tests/db/test_schema.sql` test 1** (lines 9–17) — the exact table list now has 14 tables
   (the 10 original plus the 4 weather tables) and the PASS message says 14. This is the one
   place where your edits to that file may conflict: keep both changes when merging.

## How schema tests must run from now on

The live database (`kalbt` in `kalbt-recon-db`) holds loaded data: 1 underlying, 1 series,
7 events, 42 markets, 41,158 candles, plus the KNYC station. `tests/db/test_schema.sql` inserts
its own sample rows (series `KXHIGHNY`, which now exists) and ends by checking the tables are
empty, so **it fails against the live database**. Run it against a throwaway database instead:

```bash
docker exec kalbt-recon-db sh -c 'createdb -U "$POSTGRES_USER" kalbt_schema_test'
docker exec -i kalbt-recon-db sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < db/schema.sql
docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_schema.sql
docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_weather_schema.sql
docker exec kalbt-recon-db sh -c 'dropdb -U "$POSTGRES_USER" kalbt_schema_test'
```

With `dev`'s schema this gives 55 PASS for `tests/db/test_schema.sql`. Your new market-freeze
cases should be checked the same way. The "Run the tests" command in `db/README.md` → Usage
still points at the live database; since you are editing that section, please replace it with
the commands above.

Your unit 008 plan applies `ALTER TABLE market ADD COLUMN is_settled …` and the guard trigger to
the live database: that is still fine (all 42 loaded markets are settled and become frozen).

## Unit numbers in use

Track A: 002, 003, 006 (weather dump), 007 (weather schema), 009 (weather loader, next).
Track B: 001, 004 (ingestion QA gate), 008 (market freeze).
