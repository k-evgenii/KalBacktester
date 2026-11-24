-- Tests for the views market_bracket and event_outcome in db/schema.sql
-- (spec 011-bracket-outcome-views, "Agreed tests" 1-8; test 9 is tests/db/test_schema.sql and
-- tests/db/test_weather_schema.sql run unchanged).
-- Run against a THROWAWAY database built from db/schema.sql (recipe in db/README.md, Usage):
--   docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_views.sql
-- Everything runs in one transaction that is rolled back. It creates its own parent rows and
-- does not depend on loaded data. All markets stay unsettled (result NULL).
--
-- Event days used (UNIQUE (underlying_id, event_date) means one event per day):
--   A 2026-06-01  tests 1-4, 6    B 2026-06-02  tests 3, 6 (no official value)
--   C 2026-06-03  high 52 (test 5)   D 2026-06-04  high 59 (test 5)   E 2026-06-05  high 60 (test 5)
--   G 2026-06-07  nws_cli value only (test 7)
--   H 2026-06-08  NULL and unknown strike_type markets (test 8)
-- Ladder of every event A-E and G (real shape): less cap 52; between 52-53, 54-55, 56-57,
-- 58-59; greater floor 59. Market tickers are '<event>-<suffix>' with suffixes
-- L, B52, B54, B56, B58, G.

BEGIN;

-- Parents: one station, one underlying linked to it, one series
INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name,
                     std_utc_offset_h, cli_pil, iem_id, iem_network)
VALUES ('KTST', 'TEST STATION', 40.0, -73.0, 10, 'America/New_York', -5, 'CLITST', 'TST', 'NY_ASOS');

INSERT INTO underlying (underlying_id, description, station_id)
VALUES ('TST_HIGH', 'View test underlying', 'KTST');

INSERT INTO series (ticker, underlying_id, title)
VALUES ('SER-TST', 'TST_HIGH', 'View test series');

-- Events A and B, each with the six-market ladder
INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)
VALUES ('EVT-A', 'SER-TST', 'TST_HIGH', '2026-06-01', 'test'),
       ('EVT-B', 'SER-TST', 'TST_HIGH', '2026-06-02', 'test');

INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike, open_time, close_time)
SELECT e.ticker || '-' || l.suffix, e.ticker, l.strike_type, l.floor_strike, l.cap_strike,
       '2026-05-01 00:00+00', '2026-07-01 00:00+00'
FROM event e
CROSS JOIN (VALUES
    ('L',   'less',    NULL::numeric, 52::numeric),
    ('B52', 'between', 52,            53),
    ('B54', 'between', 54,            55),
    ('B56', 'between', 56,            57),
    ('B58', 'between', 58,            59),
    ('G',   'greater', 59,            NULL)
) AS l(suffix, strike_type, floor_strike, cap_strike)
WHERE e.ticker IN ('EVT-A', 'EVT-B');

-- ---------------------------------------------------------------------------
-- Test 1: market_bracket bounds and category for event A
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    got text;
BEGIN
    SELECT string_agg(format('(%s,%s,%s)',
                             coalesce(temp_lo::text, 'NULL'),
                             coalesce(temp_hi::text, 'NULL'),
                             coalesce(category, 'NULL')),
                      ' ' ORDER BY coalesce(temp_lo, -999))
    INTO got
    FROM market_bracket
    WHERE event_ticker = 'EVT-A';

    IF got IS DISTINCT FROM '(NULL,51,lower_tail) (52,53,middle) (54,55,middle) (56,57,middle) (58,59,middle) (60,NULL,upper_tail)' THEN
        RAISE EXCEPTION 'FAIL: market_bracket event A bounds and categories are: %', got;
    END IF;
    RAISE NOTICE 'PASS: market_bracket event A has the expected bounds and categories';
END $$;

-- ---------------------------------------------------------------------------
-- Test 2: ladder_pos for event A is 1..6, coldest bracket first
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    got text;
BEGIN
    SELECT string_agg(ladder_pos || ':' || market_ticker, ' ' ORDER BY ladder_pos)
    INTO got
    FROM market_bracket
    WHERE event_ticker = 'EVT-A';

    IF got IS DISTINCT FROM '1:EVT-A-L 2:EVT-A-B52 3:EVT-A-B54 4:EVT-A-B56 5:EVT-A-B58 6:EVT-A-G' THEN
        RAISE EXCEPTION 'FAIL: ladder_pos event A is: %', got;
    END IF;
    RAISE NOTICE 'PASS: ladder_pos for event A is 1..6 coldest first';
END $$;

-- Extra: ladder_pos restarts at 1 in event B (partitioned by event)
DO $$
DECLARE
    got text;
BEGIN
    SELECT string_agg(ladder_pos::text, ',' ORDER BY ladder_pos)
    INTO got
    FROM market_bracket
    WHERE event_ticker = 'EVT-B';

    IF got IS DISTINCT FROM '1,2,3,4,5,6' THEN
        RAISE EXCEPTION 'FAIL: ladder_pos event B is: %', got;
    END IF;
    RAISE NOTICE 'PASS: ladder_pos restarts at 1 in each event';
END $$;

-- ---------------------------------------------------------------------------
-- Test 3: before any official value, event_outcome has 12 rows, all NULL outcome columns
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n_rows bigint;
    n_settled bigint;
    n_expected bigint;
    n_result bigint;
BEGIN
    SELECT count(*),
           count(settled_high_f),
           count(expected_result),
           count(result)
    INTO n_rows, n_settled, n_expected, n_result
    FROM event_outcome;

    IF n_rows <> 12 THEN
        RAISE EXCEPTION 'FAIL: event_outcome has % rows, expected 12', n_rows;
    END IF;
    IF n_settled <> 0 THEN
        RAISE EXCEPTION 'FAIL: % rows have a settled_high_f before any official value', n_settled;
    END IF;
    IF n_expected <> 0 THEN
        RAISE EXCEPTION 'FAIL: % rows have an expected_result before any official value', n_expected;
    END IF;
    IF n_result <> 0 THEN
        RAISE EXCEPTION 'FAIL: % rows have a result although all markets are unsettled', n_result;
    END IF;
    RAISE NOTICE 'PASS: event_outcome has 12 rows with NULL settled_high_f, expected_result and result';
END $$;

-- ---------------------------------------------------------------------------
-- Test 4: official value 51 for event A day: yes only for the less market
-- ---------------------------------------------------------------------------
INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
VALUES ('KTST', '2026-06-01', 'kalshi', 51, now(), 'EVT-A');

DO $$
DECLARE
    yes_markets text;
    n_no bigint;
    n_settled bigint;
    n_b_settled bigint;
BEGIN
    SELECT string_agg(market_ticker, ',' ORDER BY market_ticker)
    INTO yes_markets
    FROM event_outcome
    WHERE event_ticker = 'EVT-A' AND expected_result = 'yes';

    SELECT count(*) INTO n_no
    FROM event_outcome
    WHERE event_ticker = 'EVT-A' AND expected_result = 'no';

    SELECT count(*) INTO n_settled
    FROM event_outcome
    WHERE event_ticker = 'EVT-A' AND settled_high_f = 51;

    SELECT count(settled_high_f) INTO n_b_settled
    FROM event_outcome
    WHERE event_ticker = 'EVT-B';

    IF yes_markets IS DISTINCT FROM 'EVT-A-L' THEN
        RAISE EXCEPTION 'FAIL: high 51 gives yes for: %', yes_markets;
    END IF;
    IF n_no <> 5 THEN
        RAISE EXCEPTION 'FAIL: high 51 gives % no rows, expected 5', n_no;
    END IF;
    IF n_settled <> 6 THEN
        RAISE EXCEPTION 'FAIL: settled_high_f = 51 on % rows of event A, expected 6', n_settled;
    END IF;
    IF n_b_settled <> 0 THEN
        RAISE EXCEPTION 'FAIL: event B (other day) got a settled_high_f';
    END IF;
    RAISE NOTICE 'PASS: high 51 gives yes only for the less market, no for the other five, event B untouched';
END $$;

-- ---------------------------------------------------------------------------
-- Test 5: boundary cases, each with its own event day and official value
-- ---------------------------------------------------------------------------
INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)
VALUES ('EVT-C', 'SER-TST', 'TST_HIGH', '2026-06-03', 'test'),
       ('EVT-D', 'SER-TST', 'TST_HIGH', '2026-06-04', 'test'),
       ('EVT-E', 'SER-TST', 'TST_HIGH', '2026-06-05', 'test');

INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike, open_time, close_time)
SELECT e.ticker || '-' || l.suffix, e.ticker, l.strike_type, l.floor_strike, l.cap_strike,
       '2026-05-01 00:00+00', '2026-07-01 00:00+00'
FROM event e
CROSS JOIN (VALUES
    ('L',   'less',    NULL::numeric, 52::numeric),
    ('B52', 'between', 52,            53),
    ('B54', 'between', 54,            55),
    ('B56', 'between', 56,            57),
    ('B58', 'between', 58,            59),
    ('G',   'greater', 59,            NULL)
) AS l(suffix, strike_type, floor_strike, cap_strike)
WHERE e.ticker IN ('EVT-C', 'EVT-D', 'EVT-E');

INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
VALUES ('KTST', '2026-06-03', 'kalshi', 52, now(), 'EVT-C'),
       ('KTST', '2026-06-04', 'kalshi', 59, now(), 'EVT-D'),
       ('KTST', '2026-06-05', 'kalshi', 60, now(), 'EVT-E');

-- high 52: the lower edge of 52-53 is inside; the less market (high < 52 is yes) is no
DO $$
DECLARE
    yes_markets text;
BEGIN
    SELECT string_agg(market_ticker, ',' ORDER BY market_ticker)
    INTO yes_markets
    FROM event_outcome
    WHERE event_ticker = 'EVT-C' AND expected_result = 'yes';

    IF yes_markets IS DISTINCT FROM 'EVT-C-B52' THEN
        RAISE EXCEPTION 'FAIL: high 52 gives yes for: %', yes_markets;
    END IF;
    RAISE NOTICE 'PASS: high 52 is yes only for 52-53';
END $$;

-- high 59: the upper edge of 58-59 is inside; the greater market (floor 59) is no
DO $$
DECLARE
    yes_markets text;
BEGIN
    SELECT string_agg(market_ticker, ',' ORDER BY market_ticker)
    INTO yes_markets
    FROM event_outcome
    WHERE event_ticker = 'EVT-D' AND expected_result = 'yes';

    IF yes_markets IS DISTINCT FROM 'EVT-D-B58' THEN
        RAISE EXCEPTION 'FAIL: high 59 gives yes for: %', yes_markets;
    END IF;
    RAISE NOTICE 'PASS: high 59 is yes only for 58-59 (greater floor 59 is no)';
END $$;

-- high 60: only the greater market is yes
DO $$
DECLARE
    yes_markets text;
BEGIN
    SELECT string_agg(market_ticker, ',' ORDER BY market_ticker)
    INTO yes_markets
    FROM event_outcome
    WHERE event_ticker = 'EVT-E' AND expected_result = 'yes';

    IF yes_markets IS DISTINCT FROM 'EVT-E-G' THEN
        RAISE EXCEPTION 'FAIL: high 60 gives yes for: %', yes_markets;
    END IF;
    RAISE NOTICE 'PASS: high 60 is yes only for the greater market';
END $$;

-- ---------------------------------------------------------------------------
-- Test 6: every event with an official value has exactly one expected yes
-- (runs before the events of tests 7 and 8, which have no complete ladder with a value)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n_events bigint;
    bad text;
BEGIN
    SELECT count(DISTINCT event_ticker) INTO n_events
    FROM event_outcome
    WHERE settled_high_f IS NOT NULL;

    IF n_events <> 4 THEN
        RAISE EXCEPTION 'FAIL: % events have an official value, expected 4 (A, C, D, E)', n_events;
    END IF;

    SELECT string_agg(event_ticker || '=' || n_yes, ', ' ORDER BY event_ticker)
    INTO bad
    FROM (
        SELECT event_ticker, count(*) FILTER (WHERE expected_result = 'yes') AS n_yes
        FROM event_outcome
        GROUP BY event_ticker
        HAVING count(settled_high_f) > 0
           AND count(*) FILTER (WHERE expected_result = 'yes') <> 1
    ) AS x;

    IF bad IS NOT NULL THEN
        RAISE EXCEPTION 'FAIL: events without exactly one expected yes: %', bad;
    END IF;
    RAISE NOTICE 'PASS: each of the 4 events with an official value has exactly one expected yes';
END $$;

-- ---------------------------------------------------------------------------
-- Test 7: an nws_cli row alone leaves settled_high_f NULL
-- ---------------------------------------------------------------------------
INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)
VALUES ('EVT-G', 'SER-TST', 'TST_HIGH', '2026-06-07', 'test');

INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike, open_time, close_time)
SELECT e.ticker || '-' || l.suffix, e.ticker, l.strike_type, l.floor_strike, l.cap_strike,
       '2026-05-01 00:00+00', '2026-07-01 00:00+00'
FROM event e
CROSS JOIN (VALUES
    ('L',   'less',    NULL::numeric, 52::numeric),
    ('B52', 'between', 52,            53),
    ('B54', 'between', 54,            55),
    ('B56', 'between', 56,            57),
    ('B58', 'between', 58,            59),
    ('G',   'greater', 59,            NULL)
) AS l(suffix, strike_type, floor_strike, cap_strike)
WHERE e.ticker = 'EVT-G';

INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
VALUES ('KTST', '2026-06-07', 'nws_cli', 55, now(), 'CLITST');

DO $$
DECLARE
    n_rows bigint;
    n_settled bigint;
    n_expected bigint;
BEGIN
    SELECT count(*), count(settled_high_f), count(expected_result)
    INTO n_rows, n_settled, n_expected
    FROM event_outcome
    WHERE event_ticker = 'EVT-G';

    IF n_rows <> 6 THEN
        RAISE EXCEPTION 'FAIL: event G has % rows in event_outcome, expected 6', n_rows;
    END IF;
    IF n_settled <> 0 THEN
        RAISE EXCEPTION 'FAIL: nws_cli value leaked into settled_high_f on % rows', n_settled;
    END IF;
    IF n_expected <> 0 THEN
        RAISE EXCEPTION 'FAIL: nws_cli value produced an expected_result on % rows', n_expected;
    END IF;
    RAISE NOTICE 'PASS: a nws_cli row alone leaves settled_high_f and expected_result NULL';
END $$;

-- ---------------------------------------------------------------------------
-- Test 8: a market with strike_type NULL has NULL bounds, category and expected_result
-- (event H has a kalshi official value, so the NULLs come from the strike type, not from
-- a missing settled value). Extra: an unknown strike_type behaves the same.
-- ---------------------------------------------------------------------------
INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)
VALUES ('EVT-H', 'SER-TST', 'TST_HIGH', '2026-06-08', 'test');

INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike, open_time, close_time)
VALUES ('EVT-H-NULL',    'EVT-H', NULL,         NULL, NULL, '2026-05-01 00:00+00', '2026-07-01 00:00+00'),
       ('EVT-H-UNKNOWN', 'EVT-H', 'functional', 50,   60,   '2026-05-01 00:00+00', '2026-07-01 00:00+00');

INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)
VALUES ('KTST', '2026-06-08', 'kalshi', 55, now(), 'EVT-H');

DO $$
DECLARE
    r record;
BEGIN
    SELECT * INTO r FROM event_outcome WHERE market_ticker = 'EVT-H-NULL';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'FAIL: market with strike_type NULL is missing from event_outcome';
    END IF;
    IF r.settled_high_f IS DISTINCT FROM 55 THEN
        RAISE EXCEPTION 'FAIL: NULL-strike market has settled_high_f %, expected 55', r.settled_high_f;
    END IF;
    IF r.temp_lo IS NOT NULL OR r.temp_hi IS NOT NULL THEN
        RAISE EXCEPTION 'FAIL: NULL-strike market has bounds (%, %)', r.temp_lo, r.temp_hi;
    END IF;
    IF r.category IS NOT NULL THEN
        RAISE EXCEPTION 'FAIL: NULL-strike market has category %', r.category;
    END IF;
    IF r.expected_result IS NOT NULL THEN
        RAISE EXCEPTION 'FAIL: NULL-strike market has expected_result %', r.expected_result;
    END IF;
    RAISE NOTICE 'PASS: strike_type NULL gives NULL bounds, category and expected_result';

    -- same checks in market_bracket itself
    SELECT * INTO r FROM market_bracket WHERE market_ticker = 'EVT-H-NULL';
    IF r.temp_lo IS NOT NULL OR r.temp_hi IS NOT NULL OR r.category IS NOT NULL THEN
        RAISE EXCEPTION 'FAIL: market_bracket row for NULL strike_type is not all NULL';
    END IF;
    RAISE NOTICE 'PASS: market_bracket row for strike_type NULL has NULL bounds and category';
END $$;

DO $$
DECLARE
    r record;
BEGIN
    SELECT * INTO r FROM event_outcome WHERE market_ticker = 'EVT-H-UNKNOWN';
    IF NOT FOUND THEN
        RAISE EXCEPTION 'FAIL: market with unknown strike_type is missing from event_outcome';
    END IF;
    IF r.temp_lo IS NOT NULL OR r.temp_hi IS NOT NULL
       OR r.category IS NOT NULL OR r.expected_result IS NOT NULL THEN
        RAISE EXCEPTION 'FAIL: unknown strike_type gave bounds (%, %), category %, expected %',
            r.temp_lo, r.temp_hi, r.category, r.expected_result;
    END IF;
    RAISE NOTICE 'PASS: an unknown strike_type gives NULL bounds, category and expected_result';
END $$;

-- ---------------------------------------------------------------------------
-- Extra: both views have one row per market (no row lost or doubled by the joins)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    n_market bigint;
    n_bracket bigint;
    n_outcome bigint;
BEGIN
    SELECT count(*) INTO n_market FROM market;
    SELECT count(*) INTO n_bracket FROM market_bracket;
    SELECT count(*) INTO n_outcome FROM event_outcome;

    IF n_bracket <> n_market OR n_outcome <> n_market THEN
        RAISE EXCEPTION 'FAIL: markets %, market_bracket rows %, event_outcome rows %',
            n_market, n_bracket, n_outcome;
    END IF;
    RAISE NOTICE 'PASS: market_bracket and event_outcome have one row per market (%)', n_market;
END $$;

ROLLBACK;

-- ---------------------------------------------------------------------------
-- Extra: after the rollback none of this file's rows are left
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    left_over bigint;
BEGIN
    SELECT (SELECT count(*) FROM station WHERE station_id = 'KTST')
         + (SELECT count(*) FROM underlying WHERE underlying_id = 'TST_HIGH')
         + (SELECT count(*) FROM series WHERE ticker = 'SER-TST')
         + (SELECT count(*) FROM event WHERE ticker LIKE 'EVT-%')
         + (SELECT count(*) FROM market WHERE ticker LIKE 'EVT-%')
         + (SELECT count(*) FROM official_daily WHERE station_id = 'KTST')
    INTO left_over;
    IF left_over <> 0 THEN
        RAISE EXCEPTION 'FAIL: % test rows left after rollback', left_over;
    END IF;
    RAISE NOTICE 'PASS: no test rows left after rollback';
END $$;
