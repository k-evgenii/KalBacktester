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
    volume        numeric     NOT NULL,
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
    quantity      numeric     NOT NULL,
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
    quantity    numeric     NOT NULL,
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
    net_units     numeric     NOT NULL,
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
    net_units     numeric NOT NULL,
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

-- ---------------------------------------------------------------------------
-- Weather comments
-- ---------------------------------------------------------------------------

-- underlying (added column)
COMMENT ON COLUMN underlying.station_id IS 'The weather station whose readings the underlying settles on; null until linked.';

-- station
COMMENT ON TABLE  station IS 'A weather station: location, time zone and the ids used by NWS and IEM.';
COMMENT ON COLUMN station.station_id IS 'ICAO id of the station, for example KNYC.';
COMMENT ON COLUMN station.name IS 'Station name as given by IEM.';
COMMENT ON COLUMN station.latitude IS 'Latitude in decimal degrees, -90 to 90.';
COMMENT ON COLUMN station.longitude IS 'Longitude in decimal degrees, -180 to 180.';
COMMENT ON COLUMN station.elevation_m IS 'Elevation in metres; null if unknown.';
COMMENT ON COLUMN station.tz_name IS 'IANA time zone name, for example America/New_York.';
COMMENT ON COLUMN station.std_utc_offset_h IS 'Local standard time offset from UTC in whole hours (no daylight saving), for example -5; it fixes the climate day.';
COMMENT ON COLUMN station.cli_pil IS 'NWS daily climate report product id, for example CLINYC; null if not used.';
COMMENT ON COLUMN station.iem_id IS 'Station id in the IEM archive, for example NYC.';
COMMENT ON COLUMN station.iem_network IS 'IEM network the station belongs to, for example NY_ASOS.';

-- official_daily
COMMENT ON TABLE  official_daily IS 'Official daily high per station and climate day from one source; append-only.';
COMMENT ON COLUMN official_daily.station_id IS 'The station the value is for.';
COMMENT ON COLUMN official_daily.climate_date IS 'The local standard time day the value covers.';
COMMENT ON COLUMN official_daily.source IS 'Where the value comes from: nws_cli (NWS daily climate report) or kalshi (Kalshi settled value).';
COMMENT ON COLUMN official_daily.high_f IS 'Daily high in degrees Fahrenheit, -60 to 140.';
COMMENT ON COLUMN official_daily.high_time_lst IS 'Time of the high in local standard time as printed in the report, for example 247 PM; nws_cli only, otherwise null.';
COMMENT ON COLUMN official_daily.low_f IS 'Daily low in degrees Fahrenheit, at most high_f; nws_cli only, otherwise null.';
COMMENT ON COLUMN official_daily.issued_at IS 'When the value became known (UTC).';
COMMENT ON COLUMN official_daily.reference IS 'NWS product id or Kalshi event ticker the value was taken from.';

-- observation_hourly
COMMENT ON TABLE  observation_hourly IS 'One IEM ASOS station report per station and observation time; append-only.';
COMMENT ON COLUMN observation_hourly.station_id IS 'The station that made the report.';
COMMENT ON COLUMN observation_hourly.observed_at IS 'When the report was made (UTC).';
COMMENT ON COLUMN observation_hourly.temp_f IS 'Air temperature in degrees Fahrenheit, -80 to 140; null if missing.';
COMMENT ON COLUMN observation_hourly.dewpoint_f IS 'Dew point in degrees Fahrenheit; null if missing.';
COMMENT ON COLUMN observation_hourly.rel_humidity_pct IS 'Relative humidity in percent, 0 to 100; null if missing.';
COMMENT ON COLUMN observation_hourly.wind_speed_kt IS 'Wind speed in knots, 0 or more; null if missing.';
COMMENT ON COLUMN observation_hourly.precip_in IS 'Precipitation in inches, 0 or more; null if missing.';
COMMENT ON COLUMN observation_hourly.metar IS 'The raw METAR report; its T-group holds temperature in tenths of degrees Celsius.';

-- forecast_hourly
COMMENT ON TABLE  forecast_hourly IS 'Open-Meteo previous-runs model forecast per station, model, valid time and lead day; append-only.';
COMMENT ON COLUMN forecast_hourly.station_id IS 'The station the forecast is for.';
COMMENT ON COLUMN forecast_hourly.model IS 'Weather model name.';
COMMENT ON COLUMN forecast_hourly.valid_time IS 'The hour the forecast is valid for (UTC).';
COMMENT ON COLUMN forecast_hourly.lead_day IS 'The value was predicted 24 hours times lead_day before valid_time; 0 is the latest run; 0 to 7.';
COMMENT ON COLUMN forecast_hourly.temp_f IS 'Forecast air temperature in degrees Fahrenheit, -80 to 140; null if missing.';
COMMENT ON COLUMN forecast_hourly.dewpoint_f IS 'Forecast dew point in degrees Fahrenheit; null if missing.';
COMMENT ON COLUMN forecast_hourly.cloud_cover_pct IS 'Forecast cloud cover in percent, 0 to 100; null if missing.';
COMMENT ON COLUMN forecast_hourly.wind_speed_mph IS 'Forecast wind speed in miles per hour, 0 or more; null if missing.';
COMMENT ON COLUMN forecast_hourly.precip_in IS 'Forecast precipitation in inches, 0 or more; null if missing.';
COMMENT ON COLUMN forecast_hourly.shortwave_wm2 IS 'Forecast shortwave radiation in watts per square metre, 0 or more; null if missing.';
