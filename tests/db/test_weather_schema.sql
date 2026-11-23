-- Tests for the weather tables in db/schema.sql (spec 007-weather-schema, "Agreed tests" 1-6).
-- Test 7 is tests/db/test_schema.sql itself.
-- Run against a THROWAWAY database built from db/schema.sql (see the spec's "Done when"):
--   docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_weather_schema.sql
-- Everything runs in one transaction that is rolled back. It creates its own parent rows and
-- does not depend on loaded data. Every expected error is caught inside its own DO block
-- (or its own nested block), so the transaction is never aborted.

BEGIN;

-- Parents: one station and one underlying linked to it (names do not clash with real data)
INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name,
                     std_utc_offset_h, cli_pil, iem_id, iem_network)
VALUES ('KTST', 'TEST STATION', 40.0, -73.0, 10, 'America/New_York', -5, 'CLITST', 'TST', 'NY_ASOS');

INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('WX_TEST', 'Weather schema test underlying', 'KTST');

-- ---------------------------------------------------------------------------
-- Test 1: each CHECK refuses one bad row (check_violation)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    c record;
BEGIN
    FOR c IN SELECT * FROM (VALUES
        ('station latitude 91',
         $q$INSERT INTO station VALUES ('KBAD', 'x', 91, 0, NULL, 'UTC', 0, NULL, 'x', 'x')$q$),
        ('station latitude -91',
         $q$INSERT INTO station VALUES ('KBAD', 'x', -91, 0, NULL, 'UTC', 0, NULL, 'x', 'x')$q$),
        ('station longitude 181',
         $q$INSERT INTO station VALUES ('KBAD', 'x', 0, 181, NULL, 'UTC', 0, NULL, 'x', 'x')$q$),
        ('station std_utc_offset_h 15',
         $q$INSERT INTO station VALUES ('KBAD', 'x', 0, 0, NULL, 'UTC', 15, NULL, 'x', 'x')$q$),
        ('official_daily source twc',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
            VALUES ('KTST', '2026-06-01', 'twc', 70, now(), 'r')$q$),
        ('official_daily high 150',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
            VALUES ('KTST', '2026-06-01', 'nws_cli', 150, now(), 'r')$q$),
        ('official_daily high -61',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
            VALUES ('KTST', '2026-06-01', 'nws_cli', -61, now(), 'r')$q$),
        ('official_daily low above high',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, low_f, issued_at, reference)
            VALUES ('KTST', '2026-06-01', 'nws_cli', 50, 60, now(), 'r')$q$),
        ('official_daily kalshi row with high_time_lst',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst, issued_at, reference)
            VALUES ('KTST', '2026-06-01', 'kalshi', 70, '247 PM', now(), 'r')$q$),
        ('official_daily kalshi row with low_f',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, low_f, issued_at, reference)
            VALUES ('KTST', '2026-06-01', 'kalshi', 70, 50, now(), 'r')$q$),
        ('observation_hourly temp 200',
         $q$INSERT INTO observation_hourly (station_id, observed_at, temp_f, metar)
            VALUES ('KTST', '2026-06-01 12:00+00', 200, 'm')$q$),
        ('observation_hourly humidity 101',
         $q$INSERT INTO observation_hourly (station_id, observed_at, rel_humidity_pct, metar)
            VALUES ('KTST', '2026-06-01 12:00+00', 101, 'm')$q$),
        ('observation_hourly negative wind',
         $q$INSERT INTO observation_hourly (station_id, observed_at, wind_speed_kt, metar)
            VALUES ('KTST', '2026-06-01 12:00+00', -1, 'm')$q$),
        ('observation_hourly negative precipitation',
         $q$INSERT INTO observation_hourly (station_id, observed_at, precip_in, metar)
            VALUES ('KTST', '2026-06-01 12:00+00', -0.1, 'm')$q$),
        ('forecast_hourly lead_day 8',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day)
            VALUES ('KTST', 'm', '2026-06-01 12:00+00', 8)$q$),
        ('forecast_hourly lead_day -1',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day)
            VALUES ('KTST', 'm', '2026-06-01 12:00+00', -1)$q$),
        ('forecast_hourly temp 200',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f)
            VALUES ('KTST', 'm', '2026-06-01 12:00+00', 1, 200)$q$),
        ('forecast_hourly cloud cover 120',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, cloud_cover_pct)
            VALUES ('KTST', 'm', '2026-06-01 12:00+00', 1, 120)$q$),
        ('forecast_hourly negative wind',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, wind_speed_mph)
            VALUES ('KTST', 'm', '2026-06-01 12:00+00', 1, -1)$q$),
        ('forecast_hourly negative precipitation',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, precip_in)
            VALUES ('KTST', 'm', '2026-06-01 12:00+00', 1, -0.1)$q$),
        ('forecast_hourly negative radiation',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, shortwave_wm2)
            VALUES ('KTST', 'm', '2026-06-01 12:00+00', 1, -1)$q$)
    ) AS t(label, stmt)
    LOOP
        BEGIN
            EXECUTE c.stmt;
            RAISE EXCEPTION 'FAIL: bad row accepted: %', c.label;
        EXCEPTION WHEN check_violation THEN
            RAISE NOTICE 'PASS: check refuses %', c.label;
        END;
    END LOOP;
END $$;

-- Boundary values are accepted (extra): the checks are not too tight.
DO $$
BEGIN
    INSERT INTO official_daily (station_id, climate_date, source, high_f, low_f, issued_at, reference)
    VALUES ('KTST', '2026-01-01', 'nws_cli', -60, -60, now(), 'edge-low'),
           ('KTST', '2026-01-02', 'nws_cli', 140, 0, now(), 'edge-high');
    INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
    VALUES ('KTST', '2026-01-01', 'kalshi', 30, now(), 'KXHIGHNY-26JAN01');
    INSERT INTO observation_hourly (station_id, observed_at, temp_f, rel_humidity_pct, wind_speed_kt, precip_in, metar)
    VALUES ('KTST', '2026-01-01 00:00+00', -80, 0, 0, 0, 'edge'),
           ('KTST', '2026-01-01 01:00+00', 140, 100, 0, 0, 'edge');
    INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f, cloud_cover_pct,
                                 wind_speed_mph, precip_in, shortwave_wm2)
    VALUES ('KTST', 'edge', '2026-01-01 00:00+00', 0, -80, 0, 0, 0, 0),
           ('KTST', 'edge', '2026-01-01 00:00+00', 7, 140, 100, 0, 0, 0);
    RAISE NOTICE 'PASS: boundary values accepted in all three weather tables';
END $$;

-- ---------------------------------------------------------------------------
-- Test 2: the three weather tables refuse UPDATE, DELETE and TRUNCATE (restrict_violation)
-- (rows from the boundary block above are the targets)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    c record;
BEGIN
    FOR c IN SELECT * FROM (VALUES
        ('official_daily update',     'UPDATE official_daily SET high_f = 1'),
        ('official_daily delete',     'DELETE FROM official_daily'),
        ('official_daily truncate',   'TRUNCATE official_daily'),
        ('observation_hourly update', 'UPDATE observation_hourly SET temp_f = 1'),
        ('observation_hourly delete', 'DELETE FROM observation_hourly'),
        ('observation_hourly truncate', 'TRUNCATE observation_hourly'),
        ('forecast_hourly update',    'UPDATE forecast_hourly SET temp_f = 1'),
        ('forecast_hourly delete',    'DELETE FROM forecast_hourly'),
        ('forecast_hourly truncate',  'TRUNCATE forecast_hourly')
    ) AS t(label, stmt)
    LOOP
        BEGIN
            EXECUTE c.stmt;
            RAISE EXCEPTION 'FAIL: % was allowed', c.label;
        EXCEPTION WHEN restrict_violation THEN
            RAISE NOTICE 'PASS: % refused', c.label;
        END;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Test 3: a weather row for a station that does not exist is refused (foreign_key_violation)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    c record;
BEGIN
    FOR c IN SELECT * FROM (VALUES
        ('official_daily',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
            VALUES ('KNOPE', '2026-06-01', 'nws_cli', 70, now(), 'r')$q$),
        ('observation_hourly',
         $q$INSERT INTO observation_hourly (station_id, observed_at, metar)
            VALUES ('KNOPE', '2026-06-01 12:00+00', 'm')$q$),
        ('forecast_hourly',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day)
            VALUES ('KNOPE', 'm', '2026-06-01 12:00+00', 1)$q$)
    ) AS t(label, stmt)
    LOOP
        BEGIN
            EXECUTE c.stmt;
            RAISE EXCEPTION 'FAIL: % row for unknown station accepted', c.label;
        EXCEPTION WHEN foreign_key_violation THEN
            RAISE NOTICE 'PASS: % row for an unknown station refused', c.label;
        END;
    END LOOP;
END $$;

-- ---------------------------------------------------------------------------
-- Test 4: underlying.station_id must point to a real station; a referenced station
-- cannot be deleted
-- ---------------------------------------------------------------------------
DO $$
BEGIN
    BEGIN
        INSERT INTO underlying (underlying_id, description, station_id)
        VALUES ('WX_BAD', 'points nowhere', 'KNOPE');
        RAISE EXCEPTION 'FAIL: underlying with an unknown station accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: underlying with an unknown station_id refused';
    END;

    BEGIN
        UPDATE underlying SET station_id = 'KNOPE' WHERE underlying_id = 'WX_TEST';
        RAISE EXCEPTION 'FAIL: underlying updated to an unknown station';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: underlying update to an unknown station_id refused';
    END;

    BEGIN
        DELETE FROM station WHERE station_id = 'KTST';
        RAISE EXCEPTION 'FAIL: station referenced by an underlying was deleted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: delete of a station referenced by an underlying refused';
    END;
END $$;

-- Extra: a station referenced only by weather rows cannot be deleted either
-- (the boundary rows above reference KTST; use a second station with no underlying)
INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h, iem_id, iem_network)
VALUES ('KTS2', 'TEST STATION 2', 0, 0, 'UTC', 0, 'TS2', 'XX_ASOS');
INSERT INTO observation_hourly (station_id, observed_at, metar) VALUES ('KTS2', '2026-01-01 00:00+00', 'm');

DO $$
BEGIN
    DELETE FROM station WHERE station_id = 'KTS2';
    RAISE EXCEPTION 'FAIL: station referenced by an observation was deleted';
EXCEPTION WHEN foreign_key_violation THEN
    RAISE NOTICE 'PASS: delete of a station referenced by an observation refused';
END $$;

-- ---------------------------------------------------------------------------
-- Test 5: a second row with the same key is refused (unique_violation)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    c record;
BEGIN
    -- official_daily: same (station, date, source) as the kalshi row inserted above
    -- observation_hourly: same (station, observed_at) as the first boundary row
    -- forecast_hourly: same (station, model, valid_time, lead_day) as the first boundary row
    FOR c IN SELECT * FROM (VALUES
        ('official_daily',
         $q$INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
            VALUES ('KTST', '2026-01-01', 'kalshi', 31, now(), 'other')$q$),
        ('observation_hourly',
         $q$INSERT INTO observation_hourly (station_id, observed_at, temp_f, metar)
            VALUES ('KTST', '2026-01-01 00:00+00', 20, 'other')$q$),
        ('forecast_hourly',
         $q$INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f)
            VALUES ('KTST', 'edge', '2026-01-01 00:00+00', 0, 20)$q$)
    ) AS t(label, stmt)
    LOOP
        BEGIN
            EXECUTE c.stmt;
            RAISE EXCEPTION 'FAIL: duplicate key accepted in %', c.label;
        EXCEPTION WHEN unique_violation THEN
            RAISE NOTICE 'PASS: duplicate key refused in %', c.label;
        END;
    END LOOP;
END $$;

-- Extra: same day from two sources is two different keys and both are accepted
DO $$
BEGIN
    INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
    VALUES ('KTST', '2026-01-03', 'nws_cli', 31, now(), 'CLITST'),
           ('KTST', '2026-01-03', 'kalshi', 31, now(), 'KXHIGHNY-26JAN03');
    RAISE NOTICE 'PASS: same station and day from another source accepted';
EXCEPTION WHEN unique_violation THEN
    RAISE EXCEPTION 'FAIL: official_daily key wrongly ignores source';
END $$;

-- ---------------------------------------------------------------------------
-- Test 6: the seed (statements copied from db/seed_stations.sql) is applied, and
-- applying it again changes nothing
-- ---------------------------------------------------------------------------
-- The seed links NYC_HIGH, so make sure the row exists in this transaction.
INSERT INTO underlying (underlying_id, description)
VALUES ('NYC_HIGH', 'New York Central Park daily high')
ON CONFLICT DO NOTHING;

INSERT INTO station
    (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h, cli_pil, iem_id, iem_network)
VALUES
    ('KNYC', 'NEW YORK CITY', 40.779, -73.9692, 27.0, 'America/New_York', -5, 'CLINYC', 'NYC', 'NY_ASOS')
ON CONFLICT DO NOTHING;

UPDATE underlying SET station_id = 'KNYC' WHERE underlying_id = 'NYC_HIGH' AND station_id IS NULL;

DO $$
DECLARE
    st text;
    link text;
BEGIN
    SELECT s::text INTO st FROM station s WHERE station_id = 'KNYC';
    IF st IS NULL THEN
        RAISE EXCEPTION 'FAIL: KNYC missing after the seed';
    END IF;
    RAISE NOTICE 'PASS: KNYC exists after the seed';

    SELECT station_id INTO link FROM underlying WHERE underlying_id = 'NYC_HIGH';
    IF link IS DISTINCT FROM 'KNYC' THEN
        RAISE EXCEPTION 'FAIL: NYC_HIGH.station_id is %, expected KNYC', link;
    END IF;
    RAISE NOTICE 'PASS: NYC_HIGH is linked to KNYC';

    PERFORM set_config('test.knyc_before', st, true);
    PERFORM set_config('test.station_count_before', (SELECT count(*) FROM station)::text, true);
END $$;

-- second run of the same two statements
INSERT INTO station
    (station_id, name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h, cli_pil, iem_id, iem_network)
VALUES
    ('KNYC', 'NEW YORK CITY', 40.779, -73.9692, 27.0, 'America/New_York', -5, 'CLINYC', 'NYC', 'NY_ASOS')
ON CONFLICT DO NOTHING;

UPDATE underlying SET station_id = 'KNYC' WHERE underlying_id = 'NYC_HIGH' AND station_id IS NULL;

DO $$
DECLARE
    st text;
    link text;
BEGIN
    SELECT s::text INTO st FROM station s WHERE station_id = 'KNYC';
    SELECT station_id INTO link FROM underlying WHERE underlying_id = 'NYC_HIGH';
    IF st IS DISTINCT FROM current_setting('test.knyc_before')
       OR (SELECT count(*) FROM station)::text <> current_setting('test.station_count_before')
       OR link IS DISTINCT FROM 'KNYC' THEN
        RAISE EXCEPTION 'FAIL: running the seed twice changed the data';
    END IF;
    RAISE NOTICE 'PASS: running the seed again changes nothing';
END $$;

ROLLBACK;

-- ---------------------------------------------------------------------------
-- Extra: after the rollback none of this file's rows are left
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    left_over bigint;
BEGIN
    SELECT (SELECT count(*) FROM station WHERE station_id IN ('KTST', 'KTS2'))
         + (SELECT count(*) FROM underlying WHERE underlying_id = 'WX_TEST')
         + (SELECT count(*) FROM official_daily WHERE station_id IN ('KTST', 'KTS2'))
         + (SELECT count(*) FROM observation_hourly WHERE station_id IN ('KTST', 'KTS2'))
         + (SELECT count(*) FROM forecast_hourly WHERE station_id IN ('KTST', 'KTS2'))
    INTO left_over;
    IF left_over <> 0 THEN
        RAISE EXCEPTION 'FAIL: % test rows left after rollback', left_over;
    END IF;
    RAISE NOTICE 'PASS: no test rows left after rollback';
END $$;
