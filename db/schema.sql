-- Reconciliation database schema (spec 001). PostgreSQL 16.
-- Prices and money are numeric dollars, timestamps are timestamptz (UTC).
-- net_units is signed, in YES units: positive = long YES, negative = long NO.

SET timezone = 'UTC';

-- ---------------------------------------------------------------------------
-- Guard functions
-- ---------------------------------------------------------------------------

-- Append-only guard, shared by all append-only tables.
-- The custom error code lets the tests tell a refused change from a test failure.
CREATE FUNCTION forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: append-only table', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;

-- run guard: only status and finished_at may change; DELETE is refused.
CREATE FUNCTION run_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DELETE on run is not allowed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- compare the whole row except the two columns that may change
    IF (to_jsonb(NEW) - 'status' - 'finished_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'finished_at') THEN
        RAISE EXCEPTION 'only status and finished_at may be updated on run'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- 1. underlying
-- ---------------------------------------------------------------------------
CREATE TABLE underlying (
    underlying_id text PRIMARY KEY,
    description   text NOT NULL
);

-- ---------------------------------------------------------------------------
-- 2. series
-- ---------------------------------------------------------------------------
CREATE TABLE series (
    ticker        text PRIMARY KEY,
    underlying_id text NOT NULL REFERENCES underlying (underlying_id),
    title         text,
    UNIQUE (ticker, underlying_id)  -- target of the composite FK on event
);

-- ---------------------------------------------------------------------------
-- 3. event
-- ---------------------------------------------------------------------------
CREATE TABLE event (
    ticker            text PRIMARY KEY,
    series_ticker     text NOT NULL,
    underlying_id     text NOT NULL,
    event_date        date NOT NULL,
    settlement_source text NOT NULL,
    FOREIGN KEY (series_ticker, underlying_id) REFERENCES series (ticker, underlying_id),
    UNIQUE (underlying_id, event_date)
);

-- ---------------------------------------------------------------------------
-- 4. market
-- ---------------------------------------------------------------------------
CREATE TABLE market (
    ticker       text PRIMARY KEY,
    event_ticker text NOT NULL REFERENCES event (ticker),
    strike_type  text,
    floor_strike numeric,
    cap_strike   numeric,
    open_time    timestamptz NOT NULL,
    close_time   timestamptz NOT NULL,
    result       text,
    settled_at   timestamptz,
    CHECK (result IS NULL OR result IN ('yes', 'no')),
    CHECK ((result IS NULL) = (settled_at IS NULL)),
    CHECK (floor_strike IS NULL OR cap_strike IS NULL OR floor_strike <= cap_strike),
    CHECK (open_time < close_time)
);

-- ---------------------------------------------------------------------------
-- 5. candle (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE candle (
    market_ticker text        NOT NULL REFERENCES market (ticker),
    ts            timestamptz NOT NULL,
    yes_bid       numeric,
    yes_ask       numeric,
    volume        integer     NOT NULL,
    PRIMARY KEY (market_ticker, ts),
    CHECK (yes_bid IS NULL OR (yes_bid > 0 AND yes_bid < 1)),
    CHECK (yes_ask IS NULL OR (yes_ask > 0 AND yes_ask < 1)),
    CHECK (volume >= 0)
);

-- ---------------------------------------------------------------------------
-- 6. run (only status and finished_at are updatable, see run_guard)
-- ---------------------------------------------------------------------------
CREATE TABLE run (
    run_id        bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    strategy      text        NOT NULL,
    params        jsonb       NOT NULL DEFAULT '{}',
    code_version  text        NOT NULL,
    starting_cash numeric     NOT NULL,
    date_start    date        NOT NULL,
    date_end      date        NOT NULL,
    started_at    timestamptz NOT NULL DEFAULT now(),
    finished_at   timestamptz,
    status        text        NOT NULL DEFAULT 'running',
    CHECK (status IN ('running', 'done', 'failed', 'discarded')),
    CHECK (starting_cash > 0),
    CHECK (date_start <= date_end),
    CHECK ((status = 'running') = (finished_at IS NULL))
);

-- ---------------------------------------------------------------------------
-- 7. order_intent (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE order_intent (
    intent_id     bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id        bigint      NOT NULL REFERENCES run (run_id),
    decided_at    timestamptz NOT NULL,
    market_ticker text        NOT NULL REFERENCES market (ticker),
    side          text        NOT NULL,
    action        text        NOT NULL,
    quantity      integer     NOT NULL,
    reason        text,
    CHECK (side IN ('yes', 'no')),
    CHECK (action IN ('buy', 'sell')),
    CHECK (quantity > 0)
);

-- ---------------------------------------------------------------------------
-- 8. order_outcome (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE order_outcome (
    outcome_id  bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    intent_id   bigint      NOT NULL REFERENCES order_intent (intent_id),
    occurred_at timestamptz NOT NULL,
    outcome     text        NOT NULL,
    quantity    integer     NOT NULL,
    price       numeric,
    fee         numeric,
    reason_code text,
    CHECK (outcome IN ('filled', 'rejected', 'blocked')),
    CONSTRAINT outcome_fields CHECK (
        (outcome = 'filled' AND quantity > 0
                            AND price IS NOT NULL AND price > 0 AND price < 1
                            AND fee IS NOT NULL AND fee >= 0)
        OR (outcome IN ('rejected', 'blocked') AND quantity = 0 AND reason_code IS NOT NULL
            AND price IS NULL AND fee IS NULL)
    )
);

-- ---------------------------------------------------------------------------
-- 9. settlement (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE settlement (
    run_id        bigint      NOT NULL REFERENCES run (run_id),
    market_ticker text        NOT NULL REFERENCES market (ticker),
    settled_at    timestamptz NOT NULL,
    result        text        NOT NULL,
    net_units     integer     NOT NULL,
    payout        numeric     NOT NULL,
    PRIMARY KEY (run_id, market_ticker),
    CHECK (result IN ('yes', 'no')),
    CHECK (net_units <> 0),
    CHECK (payout >= 0)
);

-- ---------------------------------------------------------------------------
-- 10. position_snapshot (append-only)
-- ---------------------------------------------------------------------------
CREATE TABLE position_snapshot (
    run_id        bigint  NOT NULL REFERENCES run (run_id),
    as_of_date    date    NOT NULL,
    market_ticker text    NOT NULL REFERENCES market (ticker),
    net_units     integer NOT NULL,
    avg_cost      numeric NOT NULL,
    PRIMARY KEY (run_id, as_of_date, market_ticker),
    CHECK (net_units <> 0),
    CHECK (avg_cost > 0 AND avg_cost < 1)
);

-- ---------------------------------------------------------------------------
-- Triggers: append-only tables
-- ---------------------------------------------------------------------------
CREATE TRIGGER candle_no_update_delete
    BEFORE UPDATE OR DELETE ON candle
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER candle_no_truncate
    BEFORE TRUNCATE ON candle
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

CREATE TRIGGER order_intent_no_update_delete
    BEFORE UPDATE OR DELETE ON order_intent
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER order_intent_no_truncate
    BEFORE TRUNCATE ON order_intent
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

CREATE TRIGGER order_outcome_no_update_delete
    BEFORE UPDATE OR DELETE ON order_outcome
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER order_outcome_no_truncate
    BEFORE TRUNCATE ON order_outcome
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

CREATE TRIGGER settlement_no_update_delete
    BEFORE UPDATE OR DELETE ON settlement
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER settlement_no_truncate
    BEFORE TRUNCATE ON settlement
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

CREATE TRIGGER position_snapshot_no_update_delete
    BEFORE UPDATE OR DELETE ON position_snapshot
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER position_snapshot_no_truncate
    BEFORE TRUNCATE ON position_snapshot
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- Triggers: run
CREATE TRIGGER run_guard
    BEFORE UPDATE OR DELETE ON run
    FOR EACH ROW EXECUTE FUNCTION run_guard();
CREATE TRIGGER run_no_truncate
    BEFORE TRUNCATE ON run
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();

-- ---------------------------------------------------------------------------
-- Comments
-- ---------------------------------------------------------------------------

-- underlying
COMMENT ON TABLE  underlying IS 'A measured quantity at a location that markets settle on, for example a city temperature.';
COMMENT ON COLUMN underlying.underlying_id IS 'Our own id for the underlying, for example a city code.';
COMMENT ON COLUMN underlying.description IS 'Human-readable description of the underlying.';

-- series
COMMENT ON TABLE  series IS 'A Kalshi series: a recurring family of events on one underlying.';
COMMENT ON COLUMN series.ticker IS 'Kalshi series ticker.';
COMMENT ON COLUMN series.underlying_id IS 'The underlying this series settles on.';
COMMENT ON COLUMN series.title IS 'Kalshi series title, if known.';

-- event
COMMENT ON TABLE  event IS 'A Kalshi event: one underlying on one calendar day, holding a set of markets.';
COMMENT ON COLUMN event.ticker IS 'Kalshi event ticker.';
COMMENT ON COLUMN event.series_ticker IS 'The series this event belongs to.';
COMMENT ON COLUMN event.underlying_id IS 'The underlying; must match the underlying of the series.';
COMMENT ON COLUMN event.event_date IS 'The calendar day the event settles on.';
COMMENT ON COLUMN event.settlement_source IS 'The data source that settles the event.';

-- market
COMMENT ON TABLE  market IS 'A Kalshi market: one yes/no contract on a strike within an event.';
COMMENT ON COLUMN market.ticker IS 'Kalshi market ticker.';
COMMENT ON COLUMN market.event_ticker IS 'The event this market belongs to.';
COMMENT ON COLUMN market.strike_type IS 'Kalshi strike type (for example greater, less, between), if known.';
COMMENT ON COLUMN market.floor_strike IS 'Lower strike bound, null when the market has none.';
COMMENT ON COLUMN market.cap_strike IS 'Upper strike bound, null when the market has none.';
COMMENT ON COLUMN market.open_time IS 'When trading in the market opens (UTC).';
COMMENT ON COLUMN market.close_time IS 'When trading in the market closes (UTC).';
COMMENT ON COLUMN market.result IS 'Settlement result, yes or no; null until settled.';
COMMENT ON COLUMN market.settled_at IS 'When the market settled (UTC); null until settled.';

-- candle
COMMENT ON TABLE  candle IS 'Quote bar per market and bar end time; append-only.';
COMMENT ON COLUMN candle.market_ticker IS 'The market the bar belongs to.';
COMMENT ON COLUMN candle.ts IS 'Bar end time (UTC).';
COMMENT ON COLUMN candle.yes_bid IS 'Best YES bid in dollars, strictly between 0 and 1; null when there was no bid.';
COMMENT ON COLUMN candle.yes_ask IS 'Best YES ask in dollars, strictly between 0 and 1; null when there was no ask.';
COMMENT ON COLUMN candle.volume IS 'Contracts traded in the bar.';

-- run
COMMENT ON TABLE  run IS 'One backtest run; only status and finished_at can be updated, never deleted.';
COMMENT ON COLUMN run.run_id IS 'Generated run id.';
COMMENT ON COLUMN run.strategy IS 'Name of the strategy that was run.';
COMMENT ON COLUMN run.params IS 'Strategy parameters as JSON.';
COMMENT ON COLUMN run.code_version IS 'Code version (for example git hash) that produced the run.';
COMMENT ON COLUMN run.starting_cash IS 'Cash at the start of the run, in dollars.';
COMMENT ON COLUMN run.date_start IS 'First date covered by the run.';
COMMENT ON COLUMN run.date_end IS 'Last date covered by the run.';
COMMENT ON COLUMN run.started_at IS 'When the run was created (UTC).';
COMMENT ON COLUMN run.finished_at IS 'When the run ended (UTC); null while running.';
COMMENT ON COLUMN run.status IS 'running, done, failed or discarded.';

-- order_intent
COMMENT ON TABLE  order_intent IS 'An order the strategy decided to place; append-only.';
COMMENT ON COLUMN order_intent.intent_id IS 'Generated intent id.';
COMMENT ON COLUMN order_intent.run_id IS 'The run that made the decision.';
COMMENT ON COLUMN order_intent.decided_at IS 'When the strategy decided (UTC).';
COMMENT ON COLUMN order_intent.market_ticker IS 'The market the order is for.';
COMMENT ON COLUMN order_intent.side IS 'Contract side, yes or no.';
COMMENT ON COLUMN order_intent.action IS 'buy or sell.';
COMMENT ON COLUMN order_intent.quantity IS 'Requested number of contracts, greater than 0.';
COMMENT ON COLUMN order_intent.reason IS 'Why the strategy wanted the order, if recorded.';

-- order_outcome
COMMENT ON TABLE  order_outcome IS 'What happened to an order intent: filled, rejected or blocked; append-only.';
COMMENT ON COLUMN order_outcome.outcome_id IS 'Generated outcome id.';
COMMENT ON COLUMN order_outcome.intent_id IS 'The intent this outcome belongs to.';
COMMENT ON COLUMN order_outcome.occurred_at IS 'When the outcome happened (UTC).';
COMMENT ON COLUMN order_outcome.outcome IS 'blocked = our own rules stopped the order before the market (trading gate locked, untrusted settlement day, risk or size limit); rejected = it reached the market but could not execute (no quote, volume cap, not enough cash, nothing to sell); filled = it executed.';
COMMENT ON COLUMN order_outcome.quantity IS 'Contracts filled; 0 for rejected and blocked.';
COMMENT ON COLUMN order_outcome.price IS 'Fill price in dollars, strictly between 0 and 1; null unless filled.';
COMMENT ON COLUMN order_outcome.fee IS 'Fee in dollars, 0 or more; null unless filled.';
COMMENT ON COLUMN order_outcome.reason_code IS 'Why the order was rejected or blocked; required for those, optional for filled.';

-- settlement
COMMENT ON TABLE  settlement IS 'Settlement of a run position in a market; append-only.';
COMMENT ON COLUMN settlement.run_id IS 'The run that held the position.';
COMMENT ON COLUMN settlement.market_ticker IS 'The market that settled.';
COMMENT ON COLUMN settlement.settled_at IS 'When the market settled (UTC).';
COMMENT ON COLUMN settlement.result IS 'Settlement result, yes or no.';
COMMENT ON COLUMN settlement.net_units IS 'Signed position in YES units at settlement (positive = long YES, negative = long NO), never 0.';
COMMENT ON COLUMN settlement.payout IS 'Dollars paid out on the position, 0 or more.';

-- position_snapshot
COMMENT ON TABLE  position_snapshot IS 'End-of-day open position of a run in a market; append-only.';
COMMENT ON COLUMN position_snapshot.run_id IS 'The run holding the position.';
COMMENT ON COLUMN position_snapshot.as_of_date IS 'The date the snapshot is taken for.';
COMMENT ON COLUMN position_snapshot.market_ticker IS 'The market the position is in.';
COMMENT ON COLUMN position_snapshot.net_units IS 'Signed position in YES units (positive = long YES, negative = long NO), never 0.';
COMMENT ON COLUMN position_snapshot.avg_cost IS 'Average cost per unit in dollars, strictly between 0 and 1.';
