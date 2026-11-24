-- Portfolio ledger core (spec C01). PostgreSQL 16. Applied after db/schema.sql.
-- Money and prices are numeric dollars, timestamps are timestamptz (UTC).
-- Positions are signed in YES units: positive = long YES, negative = long NO.
-- Everything here is a function or a view over the journals; no tables.

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
