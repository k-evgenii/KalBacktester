# A019 — Seed every active US daily-high city (stations, underlyings, series)

Track: A. Status: APPROVED (overnight plan, PLAYBOOK §7, user 2026-11-24). The playbook's A019
("all US cities") is split in two for size: **A019 seeds the reference data**, **A020 generalises the
code** from KXHIGHNY to any series and proves one non-NY city end to end. The backfill runner
becomes A021, the query layer A022, the coverage report A023.

## Goal
Discover every US daily-high-temperature series that has markets in the backfill window
(2026-08-24 … 2026-11-22), identify its settlement station from the rules text, take the station's
coordinates and time zone from IEM, and write one data migration that adds the stations, one
underlying per station and the series. Apply it to the live database through `db/migrate.py`.

## Not built
- No change to ingest code (A020), no market or weather data loaded (A020/A021).
- International series (Celsius, no NWS report), `KXHIGHUS` (national) and `KXHIGHNYD` (hourly) are
  out of scope.
- NWS-era station phrases in `rules_names`: the backfill window is entirely after the 14 Aug 2026
  switch, where every rules text carries the CLI code, so `rules_names = ['(CLIxxx)']` suffices for
  tonight. (KNYC keeps its three phrases.)

## Files
- `api_probe/kalshi/build_us_seed.py` (new) — discovery + writes the migration
- `db/migrations/0002_us_daily_high_stations.sql` (new, written by the script)
- `db/README.md` — one paragraph in "Migrations and backups": data-only migrations (reference rows)
  are not copied into `db/schema.sql`; a fresh database gets them by running `python -m db.migrate`.

## Hard parts (use as written)

Candidate stations (legacy `legacy/notebooks_proto/collect_weather_history.py` `STATIONS`, the CLI
code's last three letters = IEM id):
```python
STATION_NETWORK = {
    "ATL": "GA_ASOS", "AUS": "TX_ASOS", "BOS": "MA_ASOS", "DCA": "VA_ASOS", "DEN": "CO_ASOS",
    "DFW": "TX_ASOS", "EWR": "NJ_ASOS", "HOU": "TX_ASOS", "LAS": "NV_ASOS", "LAX": "CA_ASOS",
    "MDW": "IL_ASOS", "MIA": "FL_ASOS", "MSP": "MN_ASOS", "MSY": "LA_ASOS", "NYC": "NY_ASOS",
    "OKC": "OK_ASOS", "PHL": "PA_ASOS", "PHX": "AZ_ASOS", "SAN": "CA_ASOS", "SAT": "TX_ASOS",
    "SDF": "KY_ASOS", "SEA": "WA_ASOS", "SFO": "CA_ASOS", "TTN": "NJ_ASOS",
}
CODE = re.compile(r"\(CLI([A-Z]{3})\)")
WINDOW = (date(2026, 8, 24), date(2026, 11, 22))
```
Script steps (standard library + `urllib`; `User-Agent: kalbt-probe`; Kalshi ≤ 2 req/s, IEM ≤ 1 req/s;
retry 429/5xx after 10 s, at most 3 times):
1. `GET /series?category=Climate and Weather` → tickers starting with `KXHIGH`, minus `KXHIGHUS`,
   `KXHIGHNYD`.
2. Per series, page `GET /markets?series_ticker=S&limit=1000&cursor=` (live surface) and
   `GET /historical/markets?series_ticker=S&limit=1000&cursor=`; keep markets whose event day (ticker
   `%y%b%d`) is in `WINDOW`. No market in the window → the series is **inactive**, skip it.
3. Codes = `CODE.findall(rules_primary)` over the window's markets. Exactly one distinct code is
   required; none or several → report "unresolved station" and skip. `iem_id` = the code; it must
   be in `STATION_NETWORK`, else report "unknown station" and skip.
4. Several active series for one `iem_id` → keep the one with the most markets in the window; report
   the others as "duplicate series, skipped".
5. Per network (cached), `GET https://mesonet.agron.iastate.edu/geojson/network.py?network=<net>`;
   the feature with `id == iem_id` gives `sname`, `geometry.coordinates` [lon, lat],
   `properties.elevation`, `properties.tzname`. Missing → "no IEM metadata", skip.
   `std_utc_offset_h = int(ZoneInfo(tz).utcoffset(datetime(2026, 1, 15)).total_seconds() // 3600)`.
6. Write `db/migrations/0002_us_daily_high_stations.sql`, rows ordered by `station_id`, each
   statement `ON CONFLICT DO NOTHING` (KNYC / NYC_HIGH / KXHIGHNY already exist and stay untouched):
```sql
INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h,
                     cli_pil, iem_id, iem_network, rules_names)
VALUES ('K<iem>', '<sname>', <lat>, <lon>, <elev>, '<tz>', <off>, 'CLI<iem>', '<iem>', '<net>',
        ARRAY['(CLI<iem>)']) ON CONFLICT DO NOTHING;
INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('<iem>_HIGH', 'Daily maximum temperature, <sname> (K<iem>)', 'K<iem>') ON CONFLICT DO NOTHING;
INSERT INTO series (ticker, underlying_id, title)
VALUES ('<series>', '<iem>_HIGH', '<series title>') ON CONFLICT DO NOTHING;
```
   Header comment: generation date, the window, and the skipped series with their reasons.
7. Print a table: series, iem_id, markets in window, station name, status (seeded / skipped + reason),
   and `requests: <n>`.

Apply: `uv run python -m db.migrate` (takes a backup first, records `0002_us_daily_high_stations`).

## Agreed tests (`tests/db/check_us_seed.py`, qa-tester)
1. The migration file is deterministic: running the script a second time writes byte-identical SQL
   (the qa-tester may compare against a copy).
2. For every seeded station: `station_id = 'K' || iem_id`, `cli_pil = 'CLI' || iem_id`,
   `rules_names = ARRAY['(CLI'||iem_id||')']`, coordinates within 0.01° of the IEM feature, and
   `std_utc_offset_h` equals the January UTC offset of `tz_name`.
3. For every seeded series, at least one of its markets in the window has rules text containing
   `(CLI<iem_id>)`, and no market in the window names another code.
4. No two seeded series share an underlying; KNYC, NYC_HIGH and KXHIGHNY are unchanged.
5. On a throwaway database built from `db/schema.sql`: `python -m db.migrate --dbname <it>` applies
   0002 once (row counts equal the file's INSERTs), a second run applies nothing; `tests/db/test_schema.sql`
   still passes on a fresh build (no seed rows in `db/schema.sql`).
6. Live: `schema_migration` holds 0002 and the station count equals 1 + the seeded count (KNYC
   counted once).

## Test files
- `tests/db/check_us_seed.py`


## Lead's note: test changes this unit must include (2026-11-24)
Applying 0002 live broke two existing suites; both are fixed in this unit, weakening nothing:
- `tests/db/check_migrate.py`: 1b on a fresh build now expects "applied 0002_us_daily_high_stations" on the
  first run and "nothing to apply" on the second (data migrations are not in `db/schema.sql`); 1c expects
  the migrations folder to hold exactly `0001_baseline.sql` and `0002_us_daily_high_stations.sql`.
- `tests/ingest/check_load_kxhighny_dump.py`: test 1 and the two EXTRA row checks count `underlying` and
  `series` only for `NYC_HIGH` / `KXHIGHNY` (each exactly once, with the same values as before);
  event, market and candle counts stay as they are.
(This takes the load-test change out of A020's "Test changes".)

## Done when
- the migration is applied to the live database (a backup was taken)
- `uv run python -m tests.db.check_us_seed` exits 0; every other suite still passes

If anything here is unclear or looks wrong: stop and report, do not guess.
