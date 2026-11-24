-- Portfolio ledger core (spec C01). PostgreSQL 16. Applied after db/schema.sql.
-- Money and prices are numeric dollars, timestamps are timestamptz (UTC).
-- Positions are signed in YES units: positive = long YES, negative = long NO.
-- Everything here is a function or a view over the journals; no tables.

DROP VIEW IF EXISTS portfolio_daily_underlying;
DROP VIEW IF EXISTS portfolio_daily;
DROP VIEW IF EXISTS portfolio_position_daily;
DROP VIEW IF EXISTS portfolio_market_daily;
DROP VIEW IF EXISTS portfolio_calendar;
DROP VIEW IF EXISTS fee_check;
DROP VIEW IF EXISTS portfolio_ledger;
DROP AGGREGATE IF EXISTS pos_agg(numeric, numeric);
DROP FUNCTION IF EXISTS pos_step(pos_state, numeric, numeric);
DROP TYPE IF EXISTS pos_state;
DROP FUNCTION IF EXISTS kalshi_fee(numeric, numeric, numeric);

CREATE FUNCTION kalshi_fee(quantity numeric, price numeric, fee_rate numeric) RETURNS numeric
LANGUAGE sql IMMUTABLE AS $$
    SELECT round(ceil(fee_rate * quantity * price * (1 - price) * 100) / 100, 2)
$$;

CREATE TYPE pos_state AS (net numeric, yes_cost numeric, realized numeric);

-- One event applied to the position of one run in one market (average cost).
CREATE FUNCTION pos_step(s pos_state, delta numeric, yes_price numeric) RETURNS pos_state
LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
    closed   numeric;   -- units of the old position this event closes
    released numeric;   -- cost of those units
BEGIN
    IF s.net = 0 OR sign(delta) = sign(s.net) THEN
        -- opening or adding
        s.yes_cost := s.yes_cost + abs(delta) * yes_price;
    ELSE
        -- reducing, closing or flipping
        closed := least(abs(delta), abs(s.net));
        IF closed = abs(s.net) THEN
            released := s.yes_cost;
        ELSE
            released := round(s.yes_cost * closed / abs(s.net), 10);
        END IF;
        s.realized := s.realized + sign(s.net) * (closed * yes_price - released);
        -- what is left of the old position, plus the opening leg of a flip
        s.yes_cost := s.yes_cost - released + (abs(delta) - closed) * yes_price;
    END IF;
    s.net := s.net + delta;
    RETURN s;
END;
$$;

CREATE AGGREGATE pos_agg(numeric, numeric) (
    SFUNC = pos_step, STYPE = pos_state, INITCOND = '(0,0,0)'
);

CREATE VIEW portfolio_ledger AS
WITH event AS (
    SELECT i.run_id, o.occurred_at AS ts, o.outcome_id AS seq, i.market_ticker, 'fill' AS kind,
           CASE WHEN (i.side = 'yes') = (i.action = 'buy')
                THEN o.quantity ELSE -o.quantity END AS delta,
           CASE WHEN i.side = 'yes' THEN o.price ELSE 1 - o.price END AS yes_price,
           NULL::numeric AS payout,
           o.fee
    FROM order_outcome o
    JOIN order_intent i ON i.intent_id = o.intent_id
    WHERE o.outcome = 'filled'
    UNION ALL
    SELECT s.run_id, s.settled_at, 0, s.market_ticker, 'settlement',
           -s.net_units,
           CASE WHEN s.result = 'yes' THEN 1 ELSE 0 END,
           s.payout,
           0
    FROM settlement s
),
stepped AS (
    SELECT e.*,
           pos_agg(e.delta, e.yes_price) OVER (
               PARTITION BY e.run_id, e.market_ticker ORDER BY e.ts, e.kind, e.seq
           ) AS st
    FROM event e
),
valued AS (
    SELECT s.*,
           (st).net AS net_after,
           CASE WHEN (st).net > 0 THEN (st).yes_cost
                ELSE abs((st).net) - (st).yes_cost END AS cost_basis_after,
           (st).realized AS realized_cum
    FROM stepped s
)
SELECT run_id, ts, seq, market_ticker, kind, delta, yes_price,
       CASE WHEN kind = 'settlement' THEN payout
            ELSE (realized_cum - coalesce(lag(realized_cum) OVER w, 0))
                 - (cost_basis_after - coalesce(lag(cost_basis_after) OVER w, 0))
                 - fee
       END AS cash_flow,
       fee, net_after, cost_basis_after, realized_cum
FROM valued
WINDOW w AS (PARTITION BY run_id, market_ticker ORDER BY ts, kind, seq);

CREATE VIEW fee_check AS
SELECT i.run_id, o.outcome_id, i.market_ticker, o.quantity, o.price, o.fee,
       kalshi_fee(o.quantity, o.price, (r.params ->> 'fee_rate')::numeric) AS expected_fee
FROM order_outcome o
JOIN order_intent i ON i.intent_id = o.intent_id
JOIN run r ON r.run_id = i.run_id
WHERE o.outcome = 'filled'
  AND o.fee IS DISTINCT FROM kalshi_fee(o.quantity, o.price, (r.params ->> 'fee_rate')::numeric);

-- ---------------------------------------------------------------------------
-- Comments
-- ---------------------------------------------------------------------------

COMMENT ON FUNCTION kalshi_fee(numeric, numeric, numeric) IS 'Kalshi fee in dollars for one whole order: fee_rate * quantity * price * (1 - price), rounded up to the cent.';
COMMENT ON TYPE pos_state IS 'State of one position: signed net units (YES units), total cost in YES terms, realized P&L before fees.';
COMMENT ON FUNCTION pos_step(pos_state, numeric, numeric) IS 'Applies one event (signed delta in YES units at a YES price) to a position at average cost; returns the new state.';
COMMENT ON AGGREGATE pos_agg(numeric, numeric) IS 'Running position state over events (delta, yes_price) in window order; used by portfolio_ledger.';

COMMENT ON VIEW portfolio_ledger IS 'One row per event of a run (filled order or settlement) with its cash flow and the position, cost basis and realized P&L after it.';
COMMENT ON COLUMN portfolio_ledger.run_id IS 'The run.';
COMMENT ON COLUMN portfolio_ledger.ts IS 'order_outcome.occurred_at for a fill, settlement.settled_at for a settlement.';
COMMENT ON COLUMN portfolio_ledger.seq IS 'outcome_id for a fill, 0 for a settlement; orders events at the same instant.';
COMMENT ON COLUMN portfolio_ledger.market_ticker IS 'The market.';
COMMENT ON COLUMN portfolio_ledger.kind IS 'fill or settlement.';
COMMENT ON COLUMN portfolio_ledger.delta IS 'Change of the position in YES units.';
COMMENT ON COLUMN portfolio_ledger.yes_price IS 'Price of the event in YES terms (settlement: 1 if result yes, else 0).';
COMMENT ON COLUMN portfolio_ledger.cash_flow IS 'Change of cash in dollars from the event, fee taken off; for a fill it follows what the fill does to the netted position, for a settlement it is the payout.';
COMMENT ON COLUMN portfolio_ledger.fee IS 'Fee of the event in dollars (settlement: 0).';
COMMENT ON COLUMN portfolio_ledger.net_after IS 'Position in the market after the event, signed YES units.';
COMMENT ON COLUMN portfolio_ledger.cost_basis_after IS 'Dollars paid for the position still open after the event; 0 when flat.';
COMMENT ON COLUMN portfolio_ledger.realized_cum IS 'Realized P&L of this run in this market up to and including the event, before fees.';

COMMENT ON VIEW fee_check IS 'Filled outcomes whose stored fee differs from kalshi_fee with the run''s params fee_rate. Empty means every fee agrees.';
COMMENT ON COLUMN fee_check.run_id IS 'The run.';
COMMENT ON COLUMN fee_check.outcome_id IS 'The filled outcome.';
COMMENT ON COLUMN fee_check.market_ticker IS 'The market.';
COMMENT ON COLUMN fee_check.quantity IS 'Filled quantity.';
COMMENT ON COLUMN fee_check.price IS 'Fill price in dollars, in the terms of the order side.';
COMMENT ON COLUMN fee_check.fee IS 'Fee stored on the outcome, in dollars.';
COMMENT ON COLUMN fee_check.expected_fee IS 'kalshi_fee for the fill with the run''s params fee_rate; null when the run has no fee_rate (cannot be checked).';

-- ---------------------------------------------------------------------------
-- Portfolio day by day (spec C02)
-- ---------------------------------------------------------------------------

-- One row per run and UTC day, from the first to the last day the run covers or has events on.
CREATE VIEW portfolio_calendar AS
SELECT r.run_id, d::date AS day
FROM run r
LEFT JOIN (
    SELECT run_id, min(ts)::date AS first_day, max(ts)::date AS last_day
    FROM portfolio_ledger GROUP BY run_id
) l ON l.run_id = r.run_id
CROSS JOIN LATERAL generate_series(
    least(r.date_start, l.first_day)::timestamp,
    greatest(r.date_end, l.last_day)::timestamp,
    interval '1 day'
) AS d;

-- State of each market the run has traded, at the end of each day.
CREATE VIEW portfolio_market_daily AS
WITH running AS (
    SELECT l.*,
           sum(l.cash_flow) OVER w AS cash_cum,
           sum(l.fee)       OVER w AS fees_cum
    FROM portfolio_ledger l
    WINDOW w AS (PARTITION BY l.run_id, l.market_ticker ORDER BY l.ts, l.kind, l.seq)
)
SELECT DISTINCT ON (c.run_id, c.day, r.market_ticker)
       c.run_id, c.day, r.market_ticker,
       r.net_after        AS net_units,
       r.cost_basis_after AS cost_basis,
       r.realized_cum, r.fees_cum, r.cash_cum
FROM portfolio_calendar c
JOIN running r ON r.run_id = c.run_id AND r.ts::date <= c.day
ORDER BY c.run_id, c.day, r.market_ticker, r.ts DESC, r.kind DESC, r.seq DESC;

CREATE VIEW portfolio_position_daily AS
SELECT run_id, day, market_ticker, net_units, cost_basis,
       round(cost_basis / abs(net_units), 4) AS avg_cost
FROM portfolio_market_daily
WHERE net_units <> 0;

CREATE VIEW portfolio_daily AS
WITH flow AS (
    SELECT run_id, ts::date AS day,
           -sum(cash_flow + fee) FILTER (WHERE kind = 'fill' AND cash_flow + fee < 0) AS premium_paid,
            sum(cash_flow + fee) FILTER (WHERE kind = 'fill' AND cash_flow + fee > 0) AS premium_received,
            sum(fee)                                                                  AS fees,
            sum(cash_flow) FILTER (WHERE kind = 'settlement')                         AS payouts
    FROM portfolio_ledger GROUP BY run_id, ts::date
),
state AS (
    SELECT run_id, day,
           sum(cash_cum) AS cash_cum, sum(cost_basis) AS cost_basis_open,
           sum(realized_cum) AS realized_pnl_cum, sum(fees_cum) AS fees_cum,
           count(*) FILTER (WHERE net_units <> 0) AS open_markets
    FROM portfolio_market_daily GROUP BY run_id, day
)
SELECT c.run_id, c.day, r.starting_cash,
       coalesce(f.premium_paid, 0)     AS premium_paid,
       coalesce(f.premium_received, 0) AS premium_received,
       coalesce(f.fees, 0)             AS fees,
       coalesce(f.payouts, 0)          AS payouts,
       r.starting_cash + coalesce(s.cash_cum, 0) AS cash_end,
       coalesce(s.cost_basis_open, 0)  AS cost_basis_open,
       coalesce(s.realized_pnl_cum, 0) AS realized_pnl_cum,
       coalesce(s.fees_cum, 0)         AS fees_cum,
       coalesce(s.realized_pnl_cum, 0) - coalesce(s.fees_cum, 0) AS net_pnl_cum,
       coalesce(s.open_markets, 0)     AS open_markets
FROM portfolio_calendar c
JOIN run r ON r.run_id = c.run_id
LEFT JOIN flow  f ON f.run_id = c.run_id AND f.day = c.day
LEFT JOIN state s ON s.run_id = c.run_id AND s.day = c.day;

CREATE VIEW portfolio_daily_underlying AS
SELECT c.run_id, c.day, u.underlying_id,
       coalesce(sum(m.cost_basis), 0)   AS cost_basis_open,
       coalesce(sum(m.realized_cum), 0) AS realized_pnl_cum,
       coalesce(sum(m.fees_cum), 0)     AS fees_cum,
       coalesce(sum(m.realized_cum), 0) - coalesce(sum(m.fees_cum), 0) AS net_pnl_cum,
       count(*) FILTER (WHERE m.net_units <> 0) AS open_markets
FROM portfolio_calendar c
CROSS JOIN underlying u
LEFT JOIN (
    SELECT d.*, e.underlying_id
    FROM portfolio_market_daily d
    JOIN market k ON k.ticker = d.market_ticker
    JOIN event e  ON e.ticker = k.event_ticker
) m ON m.run_id = c.run_id AND m.day = c.day AND m.underlying_id = u.underlying_id
GROUP BY c.run_id, c.day, u.underlying_id;

-- ---------------------------------------------------------------------------
-- Comments (C02)
-- ---------------------------------------------------------------------------

COMMENT ON VIEW portfolio_calendar IS 'One row per run and UTC day, from the earlier of the run''s date_start and its first event day to the later of its date_end and its last event day.';
COMMENT ON COLUMN portfolio_calendar.run_id IS 'The run.';
COMMENT ON COLUMN portfolio_calendar.day IS 'The UTC day.';

COMMENT ON VIEW portfolio_market_daily IS 'State at the end of each UTC day of every market the run has traded on or before that day, flat or not.';
COMMENT ON COLUMN portfolio_market_daily.run_id IS 'The run.';
COMMENT ON COLUMN portfolio_market_daily.day IS 'The UTC day; the state is after every event with ts::date <= day.';
COMMENT ON COLUMN portfolio_market_daily.market_ticker IS 'The market.';
COMMENT ON COLUMN portfolio_market_daily.net_units IS 'Position at the end of the day, signed YES units.';
COMMENT ON COLUMN portfolio_market_daily.cost_basis IS 'Dollars paid for the position still open at the end of the day; 0 when flat.';
COMMENT ON COLUMN portfolio_market_daily.realized_cum IS 'Realized P&L in this market up to the end of the day, before fees.';
COMMENT ON COLUMN portfolio_market_daily.fees_cum IS 'Fees in dollars paid in this market up to the end of the day.';
COMMENT ON COLUMN portfolio_market_daily.cash_cum IS 'Sum of cash_flow in this market up to the end of the day, in dollars.';

COMMENT ON VIEW portfolio_position_daily IS 'One row per run, day and market with a non-zero position at the end of the day.';
COMMENT ON COLUMN portfolio_position_daily.run_id IS 'The run.';
COMMENT ON COLUMN portfolio_position_daily.day IS 'The UTC day.';
COMMENT ON COLUMN portfolio_position_daily.market_ticker IS 'The market.';
COMMENT ON COLUMN portfolio_position_daily.net_units IS 'Position at the end of the day, signed YES units; never 0.';
COMMENT ON COLUMN portfolio_position_daily.cost_basis IS 'Dollars paid for the open position.';
COMMENT ON COLUMN portfolio_position_daily.avg_cost IS 'cost_basis / abs(net_units), rounded to 4 decimals; for display only.';

COMMENT ON VIEW portfolio_daily IS 'One row per run and UTC day: the day''s cash movements and the book at the end of the day. Cash end plus cost basis open equals starting cash plus net P&L, exactly.';
COMMENT ON COLUMN portfolio_daily.run_id IS 'The run.';
COMMENT ON COLUMN portfolio_daily.day IS 'The UTC day.';
COMMENT ON COLUMN portfolio_daily.starting_cash IS 'run.starting_cash in dollars.';
COMMENT ON COLUMN portfolio_daily.premium_paid IS 'That day only: cash paid by fills that cost cash, before fees, in dollars.';
COMMENT ON COLUMN portfolio_daily.premium_received IS 'That day only: cash returned by fills that returned cash, before fees, in dollars.';
COMMENT ON COLUMN portfolio_daily.fees IS 'That day only: fees in dollars.';
COMMENT ON COLUMN portfolio_daily.payouts IS 'That day only: settlement payouts in dollars.';
COMMENT ON COLUMN portfolio_daily.cash_end IS 'starting_cash plus all cash_flow up to the end of the day, in dollars.';
COMMENT ON COLUMN portfolio_daily.cost_basis_open IS 'Dollars paid for positions still open at the end of the day.';
COMMENT ON COLUMN portfolio_daily.realized_pnl_cum IS 'Realized P&L up to the end of the day, before fees.';
COMMENT ON COLUMN portfolio_daily.fees_cum IS 'All fees up to the end of the day, in dollars.';
COMMENT ON COLUMN portfolio_daily.net_pnl_cum IS 'realized_pnl_cum minus fees_cum.';
COMMENT ON COLUMN portfolio_daily.open_markets IS 'Number of markets with a non-zero position at the end of the day.';

COMMENT ON VIEW portfolio_daily_underlying IS 'One row per run, day and underlying (every row of underlying, traded or not): the end-of-day book of that underlying; 0 where the run never traded it.';
COMMENT ON COLUMN portfolio_daily_underlying.run_id IS 'The run.';
COMMENT ON COLUMN portfolio_daily_underlying.day IS 'The UTC day.';
COMMENT ON COLUMN portfolio_daily_underlying.underlying_id IS 'The underlying.';
COMMENT ON COLUMN portfolio_daily_underlying.cost_basis_open IS 'Dollars paid for positions still open in this underlying at the end of the day.';
COMMENT ON COLUMN portfolio_daily_underlying.realized_pnl_cum IS 'Realized P&L in this underlying up to the end of the day, before fees.';
COMMENT ON COLUMN portfolio_daily_underlying.fees_cum IS 'Fees in dollars in this underlying up to the end of the day.';
COMMENT ON COLUMN portfolio_daily_underlying.net_pnl_cum IS 'realized_pnl_cum minus fees_cum.';
COMMENT ON COLUMN portfolio_daily_underlying.open_markets IS 'Number of markets of this underlying with a non-zero position at the end of the day.';
