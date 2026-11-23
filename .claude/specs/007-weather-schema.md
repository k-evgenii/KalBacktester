# 007 — Weather tables in the reconciliation database

Track: A ingestion. Status: APPROVED by the user 2026-11-23 (as written; wind stays knots in observations, mph in forecasts).

## Goal
Add the weather side to the database: a `station` table with coordinates, a link from each
`underlying` to its station, and three append-only tables for the official daily values, the
hourly station observations and the hourly model forecasts. Seed the one station needed now
(KNYC) and link `NYC_HIGH` to it.

## Not built
- No loader (unit 009) and no other stations (added with the backfill, each checked against
  Kalshi's rules text).
- No derived tables (daily forecast highs, spreads, distributions): computed when needed.
- Two simplifications against the approved plan, for the user to confirm:
  1. `forecast_hourly` has **no `fetched_at`** in its key. Previous-runs values for past days
     do not change, and the QA gate's "same key, different values" check will catch it if one
     ever does. Simpler key, no duplicate rows per refetch.
  2. The daily-high sanity range is **−60 to 140 °F**, not the legacy 20–130: legacy covered
     three warm cities; Minneapolis or Denver winter highs can fall below 20.

## Files
- `db/schema.sql` — new tables appended **at the end of the file** (after Track B's additions),
  so the two tracks' changes do not overlap
- `db/seed_stations.sql` — new: the KNYC row and `NYC_HIGH → KNYC`
- `tests/db/test_weather_schema.sql` — new, written by the qa-tester
- `db/README.md` — new section "Weather tables", inserted immediately **before** the line `## Usage`
  (Track B is editing the end of that file)

## Hard parts (DDL, use as written)

```sql
-- ---------------------------------------------------------------------------
-- Weather: stations and the link from underlying
-- ---------------------------------------------------------------------------
CREATE TABLE station (
    station_id       text    PRIMARY KEY,            -- ICAO, e.g. KNYC
    name             text    NOT NULL,
    latitude         numeric NOT NULL,
    longitude        numeric NOT NULL,
    elevation_m      numeric,
    tz_name          text    NOT NULL,               -- e.g. America/New_York
    std_utc_offset_h integer NOT NULL,               -- local standard time, e.g. -5; fixes the climate day
    cli_pil          text,                           -- NWS climate report id, e.g. CLINYC
    iem_id           text    NOT NULL,               -- e.g. NYC
    iem_network      text    NOT NULL,               -- e.g. NY_ASOS
    CHECK (latitude  BETWEEN -90  AND 90),
    CHECK (longitude BETWEEN -180 AND 180),
    CHECK (std_utc_offset_h BETWEEN -12 AND 14)
);

ALTER TABLE underlying ADD COLUMN station_id text REFERENCES station (station_id);

-- ---------------------------------------------------------------------------
-- Weather: official daily values (append-only)
-- nws_cli = NWS daily climate report; kalshi = Kalshi's settled value (The Weather Company)
-- ---------------------------------------------------------------------------
CREATE TABLE official_daily (
    station_id    text        NOT NULL REFERENCES station (station_id),
    climate_date  date        NOT NULL,              -- local standard time day
    source        text        NOT NULL,
    high_f        numeric     NOT NULL,
    high_time_lst text,                              -- as printed, e.g. '247 PM'; nws_cli only
    low_f         numeric,                           -- nws_cli only
    issued_at     timestamptz NOT NULL,              -- when the value became known
    reference     text        NOT NULL,              -- NWS product id or Kalshi event ticker
    PRIMARY KEY (station_id, climate_date, source),
    CHECK (source IN ('nws_cli', 'kalshi')),
    CHECK (high_f BETWEEN -60 AND 140),
    CHECK (low_f IS NULL OR low_f <= high_f),
    CHECK (source = 'nws_cli' OR (high_time_lst IS NULL AND low_f IS NULL))
);

-- ---------------------------------------------------------------------------
-- Weather: station observations (append-only), IEM ASOS reports
-- ---------------------------------------------------------------------------
CREATE TABLE observation_hourly (
    station_id       text        NOT NULL REFERENCES station (station_id),
    observed_at      timestamptz NOT NULL,
    temp_f           numeric,
    dewpoint_f       numeric,
    rel_humidity_pct numeric,
    wind_speed_kt    numeric,
    precip_in        numeric,
    metar            text        NOT NULL,           -- raw report; T-group has tenths of degC
    PRIMARY KEY (station_id, observed_at),
    CHECK (temp_f IS NULL OR temp_f BETWEEN -80 AND 140),
    CHECK (rel_humidity_pct IS NULL OR rel_humidity_pct BETWEEN 0 AND 100),
    CHECK (wind_speed_kt IS NULL OR wind_speed_kt >= 0),
    CHECK (precip_in IS NULL OR precip_in >= 0)
);

-- ---------------------------------------------------------------------------
-- Weather: model forecasts (append-only), Open-Meteo previous runs at the station
-- lead_day N = value predicted 24 h x N before valid_time (0 = latest run)
-- ---------------------------------------------------------------------------
CREATE TABLE forecast_hourly (
    station_id      text        NOT NULL REFERENCES station (station_id),
    model           text        NOT NULL,
    valid_time      timestamptz NOT NULL,
    lead_day        smallint    NOT NULL,
    temp_f          numeric,
    dewpoint_f      numeric,
    cloud_cover_pct numeric,
    wind_speed_mph  numeric,
    precip_in       numeric,
    shortwave_wm2   numeric,
    PRIMARY KEY (station_id, model, valid_time, lead_day),
    CHECK (lead_day BETWEEN 0 AND 7),
    CHECK (temp_f IS NULL OR temp_f BETWEEN -80 AND 140),
    CHECK (cloud_cover_pct IS NULL OR cloud_cover_pct BETWEEN 0 AND 100),
    CHECK (wind_speed_mph IS NULL OR wind_speed_mph >= 0),
    CHECK (precip_in IS NULL OR precip_in >= 0),
    CHECK (shortwave_wm2 IS NULL OR shortwave_wm2 >= 0)
);

-- Append-only, same guard as the other append-only tables
CREATE TRIGGER official_daily_no_update_delete BEFORE UPDATE OR DELETE ON official_daily
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER official_daily_no_truncate BEFORE TRUNCATE ON official_daily
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER observation_hourly_no_update_delete BEFORE UPDATE OR DELETE ON observation_hourly
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER observation_hourly_no_truncate BEFORE TRUNCATE ON observation_hourly
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
CREATE TRIGGER forecast_hourly_no_update_delete BEFORE UPDATE OR DELETE ON forecast_hourly
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER forecast_hourly_no_truncate BEFORE TRUNCATE ON forecast_hourly
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
```
Plus a `COMMENT ON` for every new table and column, in the style of the existing file.

`db/seed_stations.sql`: one `INSERT INTO station … ON CONFLICT DO NOTHING` for KNYC, with the
values taken from the unit 006 dump's `station.json` (name, coordinates, elevation, time zone,
IEM id and network), `std_utc_offset_h = -5`, `cli_pil = 'CLINYC'`; then
`UPDATE underlying SET station_id = 'KNYC' WHERE underlying_id = 'NYC_HIGH' AND station_id IS NULL;`

The lead applies the new DDL to the live database once (the same statements; nothing existing
changes except the added nullable column), then the seed.

## Agreed tests
In `tests/db/test_weather_schema.sql`, inside BEGIN … ROLLBACK, one PASS notice per case:
1. Each CHECK refuses one bad row: latitude 91; offset 15; `source = 'twc'`; high 150;
   low above high; a `kalshi` row with `high_time_lst`; temperature 200; humidity 101;
   negative wind, precipitation, radiation; `lead_day = 8`; cloud cover 120.
2. `official_daily`, `observation_hourly`, `forecast_hourly` each refuse UPDATE, DELETE and
   TRUNCATE (`restrict_violation`).
3. A weather row for a station that does not exist is refused (foreign key).
4. `underlying.station_id` pointing to an unknown station is refused; a station referenced by
   an underlying cannot be deleted.
5. A second row with the same key is refused in each of the three weather tables.
6. After the seed: KNYC exists, `NYC_HIGH.station_id = 'KNYC'`, and running the seed again
   changes nothing.
7. The existing `tests/db/test_schema.sql` still passes.

## Legacy reference
`legacy/sql/schema.sql` (`daily_max`, `forecast` tables) for comparison only.

## Test files
- `tests/db/test_weather_schema.sql`

## Done when
Schema tests run against a throwaway database built from `db/schema.sql`, never the live one
(the live database holds loaded data, and `tests/db/test_schema.sql` inserts its own sample rows
and expects empty tables):
```
docker exec kalbt-recon-db sh -c 'createdb -U "$POSTGRES_USER" kalbt_schema_test'
docker exec -i kalbt-recon-db sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < db/schema.sql
docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_schema.sql
docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_weather_schema.sql
docker exec kalbt-recon-db sh -c 'dropdb -U "$POSTGRES_USER" kalbt_schema_test'
```
- both test files: no FAIL or ERROR line (`tests/db/test_schema.sql`: 55 PASS);
- live database: KNYC exists and `NYC_HIGH.station_id = 'KNYC'` (the lead applied the DDL and seed).

Lead's note (2026-11-23): `tests/db/test_schema.sql` test 1 listed exactly the 10 original tables;
its expected list now includes the 4 weather tables (expected value updated, nothing weakened).

If anything here is unclear or looks wrong: stop and report, do not guess.
