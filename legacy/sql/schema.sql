-- =========================================================================
-- kbt4 schema
--
-- Every CHECK in this file traces to a fact in KALSHI_DOMAIN.md or a decision
-- in ARCHITECTURE.md. The comment says which. If you cannot name the source,
-- the constraint should not be here.
--
-- The point of putting the rules here rather than in Python: a constraint the
-- database enforces cannot be forgotten by a fetcher written six months from
-- now. Fail-closed stops being a convention and becomes physics.
-- =========================================================================

SET timezone = 'UTC';

-- -------------------------------------------------------------------------
-- Enumerations. Real types, not free text -- a typo becomes an error at
-- INSERT rather than a row that silently never matches a filter.
-- -------------------------------------------------------------------------

CREATE TYPE strike_type   AS ENUM ('greater', 'less', 'between');
CREATE TYPE market_result AS ENUM ('yes', 'no');

-- Fail-closed vocabulary. KALSHI_DOMAIN.md section 8: these are three
-- different facts and merging them corrupts the backtest.
--   OK       usable
--   BLOCKED  settling number exists but cannot be trusted -> no entries
CREATE TYPE day_status AS ENUM ('OK', 'BLOCKED');

-- How well we know WHEN the daily max printed. Drives the blanket time stop.
-- 'none' means the stop cannot be enforced, so the day blocks. (D-gate)
CREATE TYPE time_confidence AS ENUM ('clean', 'assumed', 'none');

-- D19: which authority produced the settling number. Tagged per record so a
-- run spanning 14 Aug 2026 reports two regimes instead of blending them (I12).
CREATE TYPE settlement_source AS ENUM ('nws_cli', 'weather_company');

-- Fetch outcomes. MISSING and ERROR are NOT the same fact: MISSING means the
-- archive genuinely lacks it, ERROR means the network failed and a retry is
-- free. Conflating them writes transport failures to disk as absent data.
CREATE TYPE fetch_status AS ENUM ('OK', 'MISSING', 'ERROR');


-- -------------------------------------------------------------------------
-- city -- reference data, one row per traded location
-- -------------------------------------------------------------------------

CREATE TABLE city (
    key                 text PRIMARY KEY,          -- 'new_york'
    station             text NOT NULL,             -- 'KNYC'
    cli_pil             text NOT NULL,             -- 'CLINYC'
    -- Substring that MUST appear in the CLI's station header, else BLOCK.
    station_name_check  text NOT NULL,             -- 'CENTRAL PARK'
    -- Hours to ADD to Local Standard Time to reach UTC. The CLI reports time
    -- of max in LST year-round -- an NWS quirk, so in summer it is one hour
    -- off the local wall clock. NY/MIA 5, LAX 8.
    std_offset_hours    smallint NOT NULL,
    tz_name             text NOT NULL              -- 'America/New_York'
);


-- -------------------------------------------------------------------------
-- event -- one city-day. THE unit of settlement.
-- -------------------------------------------------------------------------

CREATE TABLE event (
    city         text NOT NULL REFERENCES city(key),
    -- LOCAL date the contract settles on. Recon confirmed markets trade before
    -- their own event date (D18), so this is never derivable from a bar's
    -- timestamp. Deriving it from bars is the most likely modelling error.
    event_date   date NOT NULL,
    series       text NOT NULL,                    -- 'KXHIGHNY'
    event_ticker text NOT NULL UNIQUE,             -- 'KXHIGHNY-26MAY14'
    PRIMARY KEY (city, event_date, series)
);


-- -------------------------------------------------------------------------
-- market -- one binary contract (one rung of the ladder)
-- -------------------------------------------------------------------------

CREATE TABLE market (
    ticker       text PRIMARY KEY,
    city         text NOT NULL,
    event_date   date NOT NULL,
    series       text NOT NULL,
    strike_type  strike_type NOT NULL,
    floor_f      smallint,                          -- degrees F
    cap_f        smallint,
    close_time   timestamptz NOT NULL,
    result       market_result,                     -- NULL until settled
    -- Rules text as published. Snapshot it: the settlement source changes on
    -- 14 Aug 2026 and the live API will stop returning the old wording (D19).
    rules_text   text,
    fetched_at   timestamptz NOT NULL DEFAULT now(),

    FOREIGN KEY (city, event_date, series) REFERENCES event(city, event_date, series),

    -- KALSHI_DOMAIN.md section 2: each strike type needs its own bounds.
    -- Without this a 'greater' contract with a NULL floor resolves every
    -- comparison to NULL and silently never settles YES.
    CONSTRAINT strike_bounds_present CHECK (
        (strike_type = 'greater' AND floor_f IS NOT NULL) OR
        (strike_type = 'less'    AND cap_f   IS NOT NULL) OR
        (strike_type = 'between' AND floor_f IS NOT NULL AND cap_f IS NOT NULL)
    ),
    CONSTRAINT strike_order CHECK (
        floor_f IS NULL OR cap_f IS NULL OR floor_f <= cap_f
    )
);

CREATE INDEX market_event_idx ON market (city, event_date);


-- -------------------------------------------------------------------------
-- bar -- one hourly candle for one contract
-- -------------------------------------------------------------------------

CREATE TABLE bar (
    ticker   text NOT NULL REFERENCES market(ticker) ON DELETE CASCADE,
    ts       timestamptz NOT NULL,                  -- bar CLOSE, UTC
    -- D1: INTEGER CENTS. Kalshi quotes 1..99 natively; the old cache held
    -- floats in [0,1] because something divided by 100 on the way in. Storing
    -- the native unit means no conversion exists to get wrong.
    yes_bid  smallint,                              -- NULL = no bid
    yes_ask  smallint,                              -- NULL = no ask
    volume   integer NOT NULL,
    PRIMARY KEY (ticker, ts),

    -- A4: 0 and 100 are NOT prices. Kalshi uses them to mean "no bid" and
    -- "no ask". They must arrive as NULL. This constraint is what catches a
    -- fetcher that forgets to translate the sentinels.
    CONSTRAINT bid_is_tradeable_price CHECK (yes_bid IS NULL OR yes_bid BETWEEN 1 AND 99),
    CONSTRAINT ask_is_tradeable_price CHECK (yes_ask IS NULL OR yes_ask BETWEEN 1 AND 99),
    CONSTRAINT volume_non_negative    CHECK (volume >= 0),

    -- Recon: bars are hourly and exactly on the hour, zero exceptions in
    -- 20,107 samples. The two-way merge in the clock depends on this, so the
    -- database enforces the assumption the design rests on.
    --
    -- NOTE the AT TIME ZONE dance. The obvious spelling,
    --     CHECK (date_trunc('hour', ts) = ts)
    -- is REJECTED: date_trunc(text, timestamptz) is STABLE, not IMMUTABLE,
    -- because the result depends on the session TimeZone. CHECK requires
    -- IMMUTABLE. Converting to a plain timestamp first fixes it, because both
    -- timezone(text, timestamptz) and date_trunc(text, timestamp) ARE immutable.
    CONSTRAINT ts_on_the_hour CHECK (
        (ts AT TIME ZONE 'UTC') = date_trunc('hour', ts AT TIME ZONE 'UTC')
    )
);

-- The global clock scans every live market in timestamp order. This index is
-- what makes that a range scan instead of a sort of the whole table.
CREATE INDEX bar_ts_idx ON bar (ts);


-- -------------------------------------------------------------------------
-- daily_max -- the official settling number for one city-day
-- -------------------------------------------------------------------------

CREATE TABLE daily_max (
    city             text NOT NULL REFERENCES city(key),
    event_date       date NOT NULL,
    status           day_status NOT NULL,
    source           settlement_source NOT NULL,
    max_f            smallint,
    max_time_utc     timestamptz,
    time_conf        time_confidence NOT NULL DEFAULT 'none',
    -- D15: settlement happens when the report is PUBLISHED, not at market
    -- close. Capital stays locked until this moment. v3 read IEM's `entered`
    -- field only to sort issuances, then threw it away.
    published_utc    timestamptz,
    record_flag      boolean NOT NULL DEFAULT false,  -- the CLI 'R' marker
    station          text,
    source_id        text,                            -- IEM product id
    reason           text,                            -- why BLOCKED
    fetched_at       timestamptz NOT NULL DEFAULT now(),
    -- D19 / I12: source is part of the KEY, not just a column. Around the
    -- 14 Aug 2026 transition you want the NWS number and The Weather Company
    -- number for the SAME city-day sitting side by side, so you can measure
    -- how often they disagree. With source outside the key, ingesting the
    -- second one would overwrite the first and the comparison becomes
    -- impossible to make after the fact.
    --
    -- Consequence: every read must name a source. The engine is configured
    -- with one; queries that forget will get two rows per day and should fail
    -- loudly rather than silently pick one.
    PRIMARY KEY (city, event_date, source),

    -- THE fail-closed rule, as a constraint rather than a convention.
    -- An OK record without a number, or a BLOCKED record without a reason,
    -- cannot physically exist in this database.
    CONSTRAINT ok_has_value CHECK (
        (status = 'OK'      AND max_f IS NOT NULL) OR
        (status = 'BLOCKED' AND reason IS NOT NULL)
    ),
    -- KALSHI_DOMAIN.md section 5: sane range check. A parse that yields 3 or
    -- 950 degrees is a parse failure, not a temperature.
    CONSTRAINT max_f_sane CHECK (max_f IS NULL OR max_f BETWEEN 20 AND 130)
);


-- -------------------------------------------------------------------------
-- fetch_log -- the request-optimisation ledger
--
-- This table is the whole point of "optimise the API requests". Once a Kalshi
-- market settles, its candles can never change -- so it is fetched exactly
-- once, ever. Marking that with `immutable` turns a 1,300-request re-run into
-- a few dozen requests for the open tail.
--
-- Concurrency is the second-order win. This is the first-order one.
-- -------------------------------------------------------------------------

CREATE TABLE fetch_log (
    resource     text NOT NULL,      -- 'kalshi_markets'|'kalshi_candles'|'cli'|'forecast'
    key          text NOT NULL,      -- series | ticker | 'city:date' | 'city:model:range'
    status       fetch_status NOT NULL,
    fetched_at   timestamptz NOT NULL DEFAULT now(),
    http_status  smallint,
    rows_written integer NOT NULL DEFAULT 0,
    -- true once the underlying can never change again. Never refetch these.
    immutable    boolean NOT NULL DEFAULT false,
    etag         text,               -- for conditional requests where supported
    error        text,               -- populated only when status = 'ERROR'
    PRIMARY KEY (resource, key),

    CONSTRAINT error_has_message CHECK (
        (status = 'ERROR' AND error IS NOT NULL) OR status <> 'ERROR'
    ),
    -- An ERROR is a transport failure and must never be treated as settled
    -- truth, however many times it recurs.
    CONSTRAINT error_never_immutable CHECK (NOT (immutable AND status = 'ERROR'))
);

-- "What still needs fetching?" is the query the fetcher runs on every start.
CREATE INDEX fetch_log_pending_idx ON fetch_log (resource, status)
    WHERE NOT immutable;


-- -------------------------------------------------------------------------
-- forecast -- the model matrix: what each model said, N days ahead
--
-- The whole point of the previous-runs endpoint: a row here is what a model
-- predicted for `valid_time` as of `lead_day` days earlier. That is
-- information you could actually have traded on. An archive/reanalysis value
-- is not, and must never end up in this table.
-- -------------------------------------------------------------------------

CREATE TABLE forecast (
    city       text NOT NULL REFERENCES city(key),
    model      text NOT NULL,               -- 'ecmwf_ifs025'
    valid_time timestamptz NOT NULL,        -- the hour being forecast, UTC
    lead_day   smallint NOT NULL,
    temp_f     real NOT NULL,
    fetched_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (city, model, valid_time, lead_day),

    -- lead_day 0 is the un-suffixed near-analysis series. It is NOT a feature:
    -- it is truth-adjacent and using it leaks the answer. Kept only as a
    -- diagnostic -- lead 1 should track lead 0 more tightly than lead 7 does,
    -- and if that ordering fails the lead semantics are inverted.
    CONSTRAINT lead_day_range CHECK (lead_day BETWEEN 0 AND 10),
    CONSTRAINT temp_sane      CHECK (temp_f BETWEEN -80 AND 140)
);

-- Feature building asks "every model's view of this hour" -- a valid_time scan.
CREATE INDEX forecast_valid_idx ON forecast (city, valid_time);


-- -------------------------------------------------------------------------
-- Convenience views
-- -------------------------------------------------------------------------

-- Bars that could actually be traded: both sides quoted and not crossed.
-- Recon reported 100% fillable, but that check never tested the 0/100
-- sentinels. Re-measure against this view once real data lands.
CREATE VIEW tradeable_bar AS
SELECT *
FROM bar
WHERE yes_bid IS NOT NULL
  AND yes_ask IS NOT NULL
  AND yes_bid <= yes_ask;

-- Days the engine may trade at all: trusted number AND a usable max time,
-- since the blanket time stop cannot be enforced without one.
CREATE VIEW tradeable_day AS
SELECT *
FROM daily_max
WHERE status = 'OK'
  AND time_conf <> 'none';
