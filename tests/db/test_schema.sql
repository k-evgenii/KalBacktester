-- Tests for db/schema.sql (spec 001-recon-schema, "Agreed tests" 1-10).
-- Run: docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < tests/db/test_schema.sql
-- Everything runs in one transaction that is rolled back; the last block checks the tables are empty.
-- Every expected error is caught inside its own DO block, so the transaction is never aborted.

BEGIN;

-- ---------------------------------------------------------------------------
-- Test 1: exactly the expected tables exist in schema public
-- (10 from spec 001, plus the 4 weather tables from spec 007)
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    found text[];
    expected text[] := ARRAY['candle','event','forecast_hourly','market','observation_hourly',
                             'official_daily','order_intent','order_outcome',
                             'position_snapshot','run','series','settlement','station','underlying'];
BEGIN
    SELECT array_agg(table_name::text ORDER BY table_name) INTO found
    FROM information_schema.tables
    WHERE table_schema = 'public' AND table_type = 'BASE TABLE';
    IF found IS DISTINCT FROM expected THEN
        RAISE EXCEPTION 'FAIL: tables in public are %, expected %', found, expected;
    END IF;
    RAISE NOTICE 'PASS: exactly the 14 expected tables exist';
END $$;

-- ---------------------------------------------------------------------------
-- Test 2: one valid chain inserts (used as parents for the later tests)
-- ---------------------------------------------------------------------------
INSERT INTO underlying (underlying_id, description) VALUES
    ('NYC', 'New York Central Park daily high'),
    ('CHI', 'Chicago Midway daily high');

INSERT INTO series (ticker, underlying_id, title)
VALUES ('KXHIGHNY', 'NYC', 'Highest temperature in NYC');

INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)
VALUES ('KXHIGHNY-26JUN01', 'KXHIGHNY', 'NYC', '2026-06-01', 'NWS CLI');

INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike,
                    open_time, close_time, result, settled_at)
VALUES ('KXHIGHNY-26JUN01-T75', 'KXHIGHNY-26JUN01', 'greater', 75, NULL,
        '2026-05-31 15:00+00', '2026-06-02 04:59+00', NULL, NULL);

INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)
VALUES ('KXHIGHNY-26JUN01-T75', '2026-06-01 12:00+00', 0.41, 0.43, 25);

INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end)
VALUES ('demo_strategy', '{"edge": 0.05}', 'v-test', 1000, '2026-06-01', '2026-06-01');

INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity, reason)
SELECT run_id, '2026-06-01 12:00+00', 'KXHIGHNY-26JUN01-T75', 'yes', 'buy', 10, r
FROM run, unnest(ARRAY['filled-case', 'rejected-case', 'blocked-case']) AS r;

INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee, reason_code)
SELECT intent_id, '2026-06-01 12:00:01+00', 'filled', 10, 0.42, 0.02, NULL
FROM order_intent WHERE reason = 'filled-case';

INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee, reason_code)
SELECT intent_id, '2026-06-01 12:00:01+00', 'rejected', 0, NULL, NULL, 'no_quote'
FROM order_intent WHERE reason = 'rejected-case';

INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee, reason_code)
SELECT intent_id, '2026-06-01 12:00:01+00', 'blocked', 0, NULL, NULL, 'gate_locked'
FROM order_intent WHERE reason = 'blocked-case';

INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout)
SELECT run_id, 'KXHIGHNY-26JUN01-T75', '2026-06-02 12:00+00', 'yes', 10, 10.00 FROM run;

INSERT INTO position_snapshot (run_id, as_of_date, market_ticker, net_units, avg_cost)
SELECT run_id, '2026-06-01', 'KXHIGHNY-26JUN01-T75', 10, 0.42 FROM run;

DO $$
DECLARE
    n_outcomes integer;
BEGIN
    SELECT count(*) INTO n_outcomes FROM order_outcome;
    IF n_outcomes <> 3 THEN
        RAISE EXCEPTION 'FAIL: valid chain has % outcomes, expected 3', n_outcomes;
    END IF;
    RAISE NOTICE 'PASS: valid chain inserted';
END $$;

-- ---------------------------------------------------------------------------
-- Test 3: each CHECK rejects a bad row (check_violation)
-- ---------------------------------------------------------------------------

-- market
DO $$ BEGIN
    BEGIN
        INSERT INTO market (ticker, event_ticker, open_time, close_time, result, settled_at)
        VALUES ('BAD-M1', 'KXHIGHNY-26JUN01', '2026-05-31 15:00+00', '2026-06-02 04:59+00',
                'maybe', '2026-06-02 12:00+00');
        RAISE EXCEPTION 'FAIL: market result maybe was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: market result maybe rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO market (ticker, event_ticker, open_time, close_time, result, settled_at)
        VALUES ('BAD-M2', 'KXHIGHNY-26JUN01', '2026-05-31 15:00+00', '2026-06-02 04:59+00',
                'yes', NULL);
        RAISE EXCEPTION 'FAIL: market result without settled_at was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: market result without settled_at rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO market (ticker, event_ticker, floor_strike, cap_strike, open_time, close_time)
        VALUES ('BAD-M3', 'KXHIGHNY-26JUN01', 80, 70, '2026-05-31 15:00+00', '2026-06-02 04:59+00');
        RAISE EXCEPTION 'FAIL: market floor above cap was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: market floor above cap rejected';
    END;
END $$;

-- extra: open_time must be before close_time
DO $$ BEGIN
    BEGIN
        INSERT INTO market (ticker, event_ticker, open_time, close_time)
        VALUES ('BAD-M4', 'KXHIGHNY-26JUN01', '2026-06-02 04:59+00', '2026-05-31 15:00+00');
        RAISE EXCEPTION 'FAIL: market open_time after close_time was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: market open_time after close_time rejected';
    END;
END $$;

-- candle
DO $$ BEGIN
    BEGIN
        INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)
        VALUES ('KXHIGHNY-26JUN01-T75', '2026-06-01 12:05+00', 0, 0.5, 10);
        RAISE EXCEPTION 'FAIL: candle bid 0 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: candle bid 0 rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)
        VALUES ('KXHIGHNY-26JUN01-T75', '2026-06-01 12:10+00', 0.4, 1, 10);
        RAISE EXCEPTION 'FAIL: candle ask 1 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: candle ask 1 rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)
        VALUES ('KXHIGHNY-26JUN01-T75', '2026-06-01 12:15+00', 0.4, 0.5, -1);
        RAISE EXCEPTION 'FAIL: candle volume -1 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: candle volume -1 rejected';
    END;
END $$;

-- extra: a candle with NULL bid and ask is allowed
DO $$ BEGIN
    BEGIN
        INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)
        VALUES ('KXHIGHNY-26JUN01-T75', '2026-06-01 12:20+00', NULL, NULL, 0);
        RAISE NOTICE 'PASS: candle with NULL bid and ask accepted';
    EXCEPTION WHEN OTHERS THEN
        RAISE EXCEPTION 'FAIL: candle with NULL bid and ask was rejected (%)', SQLERRM;
    END;
END $$;

-- run
DO $$ BEGIN
    BEGIN
        INSERT INTO run (strategy, code_version, starting_cash, date_start, date_end,
                         status, finished_at)
        VALUES ('s', 'v', 1000, '2026-06-01', '2026-06-02', 'paused', now());
        RAISE EXCEPTION 'FAIL: run status paused was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: run status paused rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO run (strategy, code_version, starting_cash, date_start, date_end)
        VALUES ('s', 'v', 0, '2026-06-01', '2026-06-02');
        RAISE EXCEPTION 'FAIL: run starting_cash 0 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: run starting_cash 0 rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO run (strategy, code_version, starting_cash, date_start, date_end)
        VALUES ('s', 'v', 1000, '2026-06-03', '2026-06-02');
        RAISE EXCEPTION 'FAIL: run date_start after date_end was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: run date_start after date_end rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO run (strategy, code_version, starting_cash, date_start, date_end,
                         status, finished_at)
        VALUES ('s', 'v', 1000, '2026-06-01', '2026-06-02', 'done', NULL);
        RAISE EXCEPTION 'FAIL: run status done without finished_at was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: run status done without finished_at rejected';
    END;
END $$;

-- extra: status running with finished_at set
DO $$ BEGIN
    BEGIN
        INSERT INTO run (strategy, code_version, starting_cash, date_start, date_end,
                         status, finished_at)
        VALUES ('s', 'v', 1000, '2026-06-01', '2026-06-02', 'running', now());
        RAISE EXCEPTION 'FAIL: run status running with finished_at was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: run status running with finished_at rejected';
    END;
END $$;

-- order_intent
DO $$ BEGIN
    BEGIN
        INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity)
        SELECT run_id, now(), 'KXHIGHNY-26JUN01-T75', 'up', 'buy', 5 FROM run;
        RAISE EXCEPTION 'FAIL: intent side up was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: intent side up rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity)
        SELECT run_id, now(), 'KXHIGHNY-26JUN01-T75', 'yes', 'hold', 5 FROM run;
        RAISE EXCEPTION 'FAIL: intent action hold was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: intent action hold rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity)
        SELECT run_id, now(), 'KXHIGHNY-26JUN01-T75', 'yes', 'buy', 0 FROM run;
        RAISE EXCEPTION 'FAIL: intent quantity 0 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: intent quantity 0 rejected';
    END;
END $$;

-- order_outcome
DO $$ BEGIN
    BEGIN
        INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee)
        SELECT intent_id, now(), 'filled', 10, NULL, 0.02
        FROM order_intent WHERE reason = 'filled-case';
        RAISE EXCEPTION 'FAIL: filled outcome without price was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: filled outcome without price rejected';
    END;
END $$;

-- extra: filled outcome with NULL fee
DO $$ BEGIN
    BEGIN
        INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee)
        SELECT intent_id, now(), 'filled', 10, 0.42, NULL
        FROM order_intent WHERE reason = 'filled-case';
        RAISE EXCEPTION 'FAIL: filled outcome with NULL fee was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: filled outcome with NULL fee rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, reason_code)
        SELECT intent_id, now(), 'rejected', 5, 'no_quote'
        FROM order_intent WHERE reason = 'rejected-case';
        RAISE EXCEPTION 'FAIL: rejected outcome with quantity 5 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: rejected outcome with quantity 5 rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, reason_code)
        SELECT intent_id, now(), 'blocked', 0, NULL
        FROM order_intent WHERE reason = 'blocked-case';
        RAISE EXCEPTION 'FAIL: blocked outcome without reason_code was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: blocked outcome without reason_code rejected';
    END;
END $$;

-- extra: unknown outcome value
DO $$ BEGIN
    BEGIN
        INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, reason_code)
        SELECT intent_id, now(), 'pending', 0, 'x'
        FROM order_intent WHERE reason = 'blocked-case';
        RAISE EXCEPTION 'FAIL: outcome pending was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: outcome pending rejected';
    END;
END $$;

-- settlement (checks fire before the primary key, so reusing the valid chain's key is fine)
DO $$ BEGIN
    BEGIN
        INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout)
        SELECT run_id, 'KXHIGHNY-26JUN01-T75', now(), 'maybe', 10, 10 FROM run;
        RAISE EXCEPTION 'FAIL: settlement result maybe was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: settlement result maybe rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout)
        SELECT run_id, 'KXHIGHNY-26JUN01-T75', now(), 'yes', 0, 0 FROM run;
        RAISE EXCEPTION 'FAIL: settlement net_units 0 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: settlement net_units 0 rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout)
        SELECT run_id, 'KXHIGHNY-26JUN01-T75', now(), 'yes', 10, -1 FROM run;
        RAISE EXCEPTION 'FAIL: settlement negative payout was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: settlement negative payout rejected';
    END;
END $$;

-- position_snapshot
DO $$ BEGIN
    BEGIN
        INSERT INTO position_snapshot (run_id, as_of_date, market_ticker, net_units, avg_cost)
        SELECT run_id, '2026-06-02', 'KXHIGHNY-26JUN01-T75', 0, 0.42 FROM run;
        RAISE EXCEPTION 'FAIL: snapshot net_units 0 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: snapshot net_units 0 rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO position_snapshot (run_id, as_of_date, market_ticker, net_units, avg_cost)
        SELECT run_id, '2026-06-02', 'KXHIGHNY-26JUN01-T75', 10, 1 FROM run;
        RAISE EXCEPTION 'FAIL: snapshot avg_cost 1 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: snapshot avg_cost 1 rejected';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- Test 4: foreign keys reject orphans (foreign_key_violation)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    BEGIN
        INSERT INTO market (ticker, event_ticker, open_time, close_time)
        VALUES ('ORPHAN-M', 'NO-SUCH-EVENT', '2026-05-31 15:00+00', '2026-06-02 04:59+00');
        RAISE EXCEPTION 'FAIL: market with unknown event was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: market with unknown event rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity)
        SELECT run_id, now(), 'NO-SUCH-MARKET', 'yes', 'buy', 5 FROM run;
        RAISE EXCEPTION 'FAIL: intent with unknown market was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: intent with unknown market rejected';
    END;
END $$;

DO $$ BEGIN
    BEGIN
        INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, reason_code)
        VALUES (-1, now(), 'blocked', 0, 'gate_locked');
        RAISE EXCEPTION 'FAIL: outcome with unknown intent was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: outcome with unknown intent rejected';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- Test 5: UNIQUE (underlying_id, event_date)
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    BEGIN
        INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)
        VALUES ('KXHIGHNY-26JUN01-B', 'KXHIGHNY', 'NYC', '2026-06-01', 'NWS CLI');
        RAISE EXCEPTION 'FAIL: second event for same underlying and day was accepted';
    EXCEPTION WHEN unique_violation THEN
        RAISE NOTICE 'PASS: second event for same underlying and day rejected';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- Test 6: composite FK (series_ticker, underlying_id) -> series
-- ---------------------------------------------------------------------------
DO $$ BEGIN
    BEGIN
        INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)
        VALUES ('KXHIGHNY-26JUN02-CHI', 'KXHIGHNY', 'CHI', '2026-06-02', 'NWS CLI');
        RAISE EXCEPTION 'FAIL: event with underlying different from its series was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: event with underlying different from its series rejected';
    END;
END $$;

-- ---------------------------------------------------------------------------
-- Test 7: append-only tables refuse UPDATE, DELETE and TRUNCATE (restrict_violation)
-- TRUNCATE uses CASCADE: without it Postgres refuses to truncate a referenced table
-- with a different error before the trigger runs.
-- ---------------------------------------------------------------------------
DO $$ BEGIN BEGIN
    UPDATE candle SET volume = volume + 1;
    RAISE EXCEPTION 'FAIL: UPDATE on candle was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: UPDATE on candle refused'; END; END $$;
DO $$ BEGIN BEGIN
    DELETE FROM candle;
    RAISE EXCEPTION 'FAIL: DELETE on candle was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: DELETE on candle refused'; END; END $$;
DO $$ BEGIN BEGIN
    TRUNCATE candle CASCADE;
    RAISE EXCEPTION 'FAIL: TRUNCATE on candle was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: TRUNCATE on candle refused'; END; END $$;

DO $$ BEGIN BEGIN
    UPDATE order_intent SET quantity = quantity + 1;
    RAISE EXCEPTION 'FAIL: UPDATE on order_intent was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: UPDATE on order_intent refused'; END; END $$;
DO $$ BEGIN BEGIN
    DELETE FROM order_intent;
    RAISE EXCEPTION 'FAIL: DELETE on order_intent was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: DELETE on order_intent refused'; END; END $$;
DO $$ BEGIN BEGIN
    TRUNCATE order_intent CASCADE;
    RAISE EXCEPTION 'FAIL: TRUNCATE on order_intent was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: TRUNCATE on order_intent refused'; END; END $$;

DO $$ BEGIN BEGIN
    UPDATE order_outcome SET reason_code = 'changed';
    RAISE EXCEPTION 'FAIL: UPDATE on order_outcome was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: UPDATE on order_outcome refused'; END; END $$;
DO $$ BEGIN BEGIN
    DELETE FROM order_outcome;
    RAISE EXCEPTION 'FAIL: DELETE on order_outcome was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: DELETE on order_outcome refused'; END; END $$;
DO $$ BEGIN BEGIN
    TRUNCATE order_outcome CASCADE;
    RAISE EXCEPTION 'FAIL: TRUNCATE on order_outcome was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: TRUNCATE on order_outcome refused'; END; END $$;

DO $$ BEGIN BEGIN
    UPDATE settlement SET payout = payout + 1;
    RAISE EXCEPTION 'FAIL: UPDATE on settlement was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: UPDATE on settlement refused'; END; END $$;
DO $$ BEGIN BEGIN
    DELETE FROM settlement;
    RAISE EXCEPTION 'FAIL: DELETE on settlement was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: DELETE on settlement refused'; END; END $$;
DO $$ BEGIN BEGIN
    TRUNCATE settlement CASCADE;
    RAISE EXCEPTION 'FAIL: TRUNCATE on settlement was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: TRUNCATE on settlement refused'; END; END $$;

DO $$ BEGIN BEGIN
    UPDATE position_snapshot SET avg_cost = 0.5;
    RAISE EXCEPTION 'FAIL: UPDATE on position_snapshot was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: UPDATE on position_snapshot refused'; END; END $$;
DO $$ BEGIN BEGIN
    DELETE FROM position_snapshot;
    RAISE EXCEPTION 'FAIL: DELETE on position_snapshot was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: DELETE on position_snapshot refused'; END; END $$;
DO $$ BEGIN BEGIN
    TRUNCATE position_snapshot CASCADE;
    RAISE EXCEPTION 'FAIL: TRUNCATE on position_snapshot was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: TRUNCATE on position_snapshot refused'; END; END $$;

-- ---------------------------------------------------------------------------
-- Test 8: run guard
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    new_status text;
BEGIN
    UPDATE run SET status = 'done', finished_at = '2026-06-02 13:00+00'
    RETURNING status INTO new_status;
    IF new_status IS DISTINCT FROM 'done' THEN
        RAISE EXCEPTION 'FAIL: run status update to done did not apply';
    END IF;
    RAISE NOTICE 'PASS: run status done with finished_at accepted';
EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION 'FAIL: run status update to done failed (%)', SQLERRM;
END $$;

DO $$ BEGIN BEGIN
    UPDATE run SET strategy = 'other_strategy';
    RAISE EXCEPTION 'FAIL: UPDATE of run.strategy was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: UPDATE of run.strategy refused'; END; END $$;

-- extra: another frozen column
DO $$ BEGIN BEGIN
    UPDATE run SET starting_cash = starting_cash + 1;
    RAISE EXCEPTION 'FAIL: UPDATE of run.starting_cash was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: UPDATE of run.starting_cash refused'; END; END $$;

DO $$ BEGIN BEGIN
    DELETE FROM run;
    RAISE EXCEPTION 'FAIL: DELETE on run was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: DELETE on run refused'; END; END $$;

DO $$ BEGIN BEGIN
    TRUNCATE run CASCADE;
    RAISE EXCEPTION 'FAIL: TRUNCATE on run was accepted';
EXCEPTION WHEN restrict_violation THEN RAISE NOTICE 'PASS: TRUNCATE on run refused'; END; END $$;

-- ---------------------------------------------------------------------------
-- Test 9: a market referenced by an order_intent cannot be deleted
-- ---------------------------------------------------------------------------
-- a market referenced only by one order_intent (no candle, settlement or snapshot)
INSERT INTO market (ticker, event_ticker, open_time, close_time)
VALUES ('KXHIGHNY-26JUN01-T80', 'KXHIGHNY-26JUN01', '2026-05-31 15:00+00', '2026-06-02 04:59+00');

INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity, reason)
SELECT run_id, '2026-06-01 12:00+00', 'KXHIGHNY-26JUN01-T80', 'no', 'buy', 5, 'fk-only-case'
FROM run;

DO $$ BEGIN
    BEGIN
        DELETE FROM market WHERE ticker = 'KXHIGHNY-26JUN01-T80';
        RAISE EXCEPTION 'FAIL: delete of a market referenced by an order_intent was accepted';
    EXCEPTION WHEN foreign_key_violation THEN
        RAISE NOTICE 'PASS: delete of a market referenced by an order_intent refused';
    END;
END $$;

ROLLBACK;

-- ---------------------------------------------------------------------------
-- Test 10: after the rollback every table is empty
-- ---------------------------------------------------------------------------
DO $$
DECLARE
    total bigint;
BEGIN
    SELECT (SELECT count(*) FROM underlying)
         + (SELECT count(*) FROM series)
         + (SELECT count(*) FROM event)
         + (SELECT count(*) FROM market)
         + (SELECT count(*) FROM candle)
         + (SELECT count(*) FROM run)
         + (SELECT count(*) FROM order_intent)
         + (SELECT count(*) FROM order_outcome)
         + (SELECT count(*) FROM settlement)
         + (SELECT count(*) FROM position_snapshot)
    INTO total;
    IF total <> 0 THEN
        RAISE EXCEPTION 'FAIL: % rows left in the tables after rollback', total;
    END IF;
    RAISE NOTICE 'PASS: all 10 tables empty after rollback';
END $$;
