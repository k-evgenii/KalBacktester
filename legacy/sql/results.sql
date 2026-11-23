-- =========================================================================
-- kbt4 results schema  (apply after schema.sql)
--
-- Stored results are only worth keeping if they are REPRODUCIBLE. A run row
-- therefore records everything needed to re-create itself: strategy identity,
-- parameters, engine configuration, code hash, and the data window. Without
-- those, a metrics table is a pile of numbers with no provenance and you will
-- not trust it in three weeks.
--
-- ONE DELIBERATE OMISSION: no foreign key from run_fill.ticker to
-- market.ticker. Results must survive a refetch. If you re-ingest markets and
-- a ticker changes or a row is deleted, an FK would cascade away your run
-- history -- destroying the record of an experiment because its INPUT moved.
-- The ticker is stored as plain text: a soft reference on purpose.
-- =========================================================================

SET timezone = 'UTC';

CREATE TYPE run_status AS ENUM ('running', 'done', 'failed');
CREATE TYPE order_side AS ENUM ('yes', 'no');
CREATE TYPE order_action AS ENUM ('buy', 'sell');

-- Why an order did not fill, in full or in part. v3 silently skipped these,
-- which makes "never traded" and "traded and lost" indistinguishable. Recon
-- showed the volume cap will bind constantly, so this is not a nicety.
CREATE TYPE reject_reason AS ENUM (
    'not_tradeable',       -- missing side or crossed quotes
    'gate_locked',         -- I7: outcome already decided
    'day_blocked',         -- untrusted settling number
    'no_prior_bar',        -- first bar of the event (D9)
    'volume_cap',          -- participation limit reached
    'insufficient_cash',   -- I1
    'nothing_to_sell',     -- sell with no position, flip selling off
    'zero_volume'          -- prior bar traded nothing
);


-- -------------------------------------------------------------------------
-- run -- one backtest execution
-- -------------------------------------------------------------------------

CREATE TABLE run (
    run_id       bigserial PRIMARY KEY,
    started_at   timestamptz NOT NULL DEFAULT now(),
    finished_at  timestamptz,
    status       run_status NOT NULL DEFAULT 'running',

    strategy     text NOT NULL,
    params       jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Engine configuration: bankroll, participation rate, fee rate, fill model,
    -- flip selling on/off, blanket time stop on/off, settlement source.
    -- JSONB rather than columns because these change as the engine grows and a
    -- column-per-knob means a migration every time you add one.
    config       jsonb NOT NULL DEFAULT '{}'::jsonb,
    -- Hash of the strategy source. Two runs with identical params but different
    -- code are different experiments, and this is the only thing that knows it.
    code_hash    text,

    date_start   date NOT NULL,
    date_end     date NOT NULL,
    cities       text[] NOT NULL,
    error        text,

    CONSTRAINT date_order  CHECK (date_start <= date_end),
    CONSTRAINT failed_has_error CHECK (status <> 'failed' OR error IS NOT NULL),
    CONSTRAINT done_has_finish  CHECK (status = 'running' OR finished_at IS NOT NULL)
);

-- Sweeps ask "have I already run this exact configuration?"
CREATE INDEX run_lookup_idx ON run (strategy, date_start, date_end);
CREATE INDEX run_params_idx ON run USING gin (params);


-- -------------------------------------------------------------------------
-- run_fill -- every executed slice
-- -------------------------------------------------------------------------

CREATE TABLE run_fill (
    run_id     bigint NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    seq        integer NOT NULL,            -- fill order within the run
    ts         timestamptz NOT NULL,
    ticker     text NOT NULL,               -- soft reference, see header
    side       order_side NOT NULL,
    action     order_action NOT NULL,
    quantity   integer NOT NULL,            -- what actually filled
    requested  integer NOT NULL,            -- what was asked for
    price      smallint NOT NULL,           -- cents, of the side traded
    fee        integer NOT NULL,            -- cents, whole-order, ceiled (I9)
    reason     text,
    PRIMARY KEY (run_id, seq),

    CONSTRAINT qty_positive   CHECK (quantity > 0),
    -- A fill can be short of the request (participation cap) but never over it.
    CONSTRAINT no_overfill    CHECK (quantity <= requested),
    CONSTRAINT price_valid    CHECK (price BETWEEN 1 AND 99),
    CONSTRAINT fee_signed     CHECK (fee >= 0)
);

CREATE INDEX run_fill_ts_idx ON run_fill (run_id, ts);


-- -------------------------------------------------------------------------
-- run_reject -- orders that filled short or not at all
-- -------------------------------------------------------------------------

CREATE TABLE run_reject (
    run_id    bigint NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    seq       integer NOT NULL,
    ts        timestamptz NOT NULL,
    ticker    text NOT NULL,
    requested integer NOT NULL,
    filled    integer NOT NULL DEFAULT 0,
    why       reject_reason NOT NULL,
    PRIMARY KEY (run_id, seq),

    CONSTRAINT reject_short CHECK (filled < requested)
);

-- "Why is this strategy not trading?" -- one GROUP BY away.
CREATE INDEX run_reject_why_idx ON run_reject (run_id, why);


-- -------------------------------------------------------------------------
-- run_equity -- the portfolio through time
--
-- One row per bar, not per trade. This is what makes the daily return series
-- (D17) and peak capital at risk (D16) computable. All values in CENTS.
-- -------------------------------------------------------------------------

CREATE TABLE run_equity (
    run_id           bigint NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    ts               timestamptz NOT NULL,
    free_cash        bigint NOT NULL,
    reserved         bigint NOT NULL,
    -- Mark-to-market equity: cash plus open positions valued at the current
    -- quote. An ESTIMATE, unlike cash, which is exact. Invariants I1-I3 apply
    -- to cash only.
    mtm_equity       bigint NOT NULL,
    -- Premium actually paid out and not yet returned. What the bankroll
    -- constraint binds on.
    capital_deployed bigint NOT NULL,
    -- D16: economic worst case = max over latent states of
    -- (deployed - payoff). Strictly smaller than deployed whenever offsetting
    -- legs are held. This is the denominator of the headline metric, and the
    -- reason the payoff matrix exists.
    capital_at_risk  bigint NOT NULL,
    PRIMARY KEY (run_id, ts),

    CONSTRAINT cash_non_negative     CHECK (free_cash >= 0),   -- I1
    CONSTRAINT reserved_non_negative CHECK (reserved >= 0),
    -- Risk can never exceed what you actually put in.
    CONSTRAINT risk_bounded          CHECK (capital_at_risk <= capital_deployed)
);


-- -------------------------------------------------------------------------
-- run_position -- inventory through time (the inventory graph)
--
-- OPTIONAL PER RUN. At ~33 live markets x ~716 timestamps this is ~24k rows
-- for a single month-long run -- trivial. For a 1,000-run sweep it is 24
-- MILLION. Gate it behind config.record_positions and leave it off for sweeps.
-- -------------------------------------------------------------------------

CREATE TABLE run_position (
    run_id   bigint NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    ts       timestamptz NOT NULL,
    ticker   text NOT NULL,
    -- D2: ONE SIGNED INTEGER in YES units. Positive long YES, negative long NO.
    net      integer NOT NULL,
    avg_cost smallint NOT NULL,
    PRIMARY KEY (run_id, ts, ticker),

    -- Flat positions are not stored; absence means flat. Storing zeros would
    -- roughly triple the table for no information.
    CONSTRAINT net_non_zero CHECK (net <> 0),
    CONSTRAINT cost_valid   CHECK (avg_cost BETWEEN 1 AND 99)
);


-- -------------------------------------------------------------------------
-- run_metric -- headline numbers, key-value
--
-- Key-value rather than a column per metric, deliberately: adding a new metric
-- must not require a migration, and sweeps compare arbitrary metrics across
-- thousands of runs. The cost is no type safety on the name -- which is why
-- the metric names live in one Python enum, not in scattered string literals.
-- -------------------------------------------------------------------------

CREATE TABLE run_metric (
    run_id bigint NOT NULL REFERENCES run(run_id) ON DELETE CASCADE,
    name   text NOT NULL,
    value  double precision,
    PRIMARY KEY (run_id, name)
);

CREATE INDEX run_metric_name_idx ON run_metric (name, value);


-- -------------------------------------------------------------------------
-- Convenience view: the leaderboard
-- -------------------------------------------------------------------------

CREATE VIEW run_summary AS
SELECT
    r.run_id,
    r.strategy,
    r.date_start,
    r.date_end,
    r.status,
    MAX(m.value) FILTER (WHERE m.name = 'profit_per_peak_risk') AS headline,
    MAX(m.value) FILTER (WHERE m.name = 'total_pnl_cents')      AS pnl_cents,
    MAX(m.value) FILTER (WHERE m.name = 'sharpe_daily')         AS sharpe_daily,
    MAX(m.value) FILTER (WHERE m.name = 'max_drawdown_pct')     AS max_dd_pct,
    (SELECT count(*) FROM run_fill f WHERE f.run_id = r.run_id) AS n_fills
FROM run r
LEFT JOIN run_metric m ON m.run_id = r.run_id
GROUP BY r.run_id;
