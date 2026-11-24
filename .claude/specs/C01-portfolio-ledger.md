# C01 — Portfolio ledger core

Track: C accounting. First unit of the approved plan (C01 ledger core, C02 day-by-day views,
C03 daily report, C04 marks and equity, C05 payoff by outcome).
STATUS: approved by the user.

## Goal
One SQL file that turns the primary events of a run (filled orders and settlements) into a
ledger: one row per event with its cash flow, and the position, cost basis and realized P&L
after it. It also provides the Kalshi fee as a SQL function and a view that lists fills whose
stored fee disagrees with it.

## Not built
No day-by-day views, no report script, no marks or equity, no payoff by outcome (units C02 to
C05). No new tables: everything is a function or a view over the existing journals, so nothing
can drift from them. `position_snapshot` is not read. `db/schema.sql` is not changed, and the
file is not applied to the live database `kalbt` in this unit.

## Files
- `db/portfolio.sql` (new)

## Interfaces

Money and prices are `numeric` dollars, quantities are `numeric`, timestamps are `timestamptz`
(UTC), as in `db/schema.sql`. Positions are signed in YES units: positive = long YES,
negative = long NO.

`db/portfolio.sql` is applied after `db/schema.sql`. It starts by dropping its own objects
(`DROP ... IF EXISTS`), so applying it twice in a row works.

Objects, in this order:

1. `kalshi_fee(quantity numeric, price numeric, fee_rate numeric) RETURNS numeric` — the fee in
   dollars for one whole order, rounded up to the cent.
2. Type `pos_state (net numeric, yes_cost numeric, realized numeric)`, function
   `pos_step(pos_state, numeric, numeric) RETURNS pos_state`, aggregate
   `pos_agg(numeric, numeric)`.
3. View `portfolio_ledger`, one row per event of a run:

   | Column | Meaning |
   |---|---|
   | `run_id` | the run |
   | `ts` | `order_outcome.occurred_at` for a fill, `settlement.settled_at` for a settlement |
   | `seq` | `outcome_id` for a fill, 0 for a settlement |
   | `market_ticker` | the market |
   | `kind` | `'fill'` or `'settlement'` |
   | `delta` | change of the position in YES units |
   | `yes_price` | price of the event in YES terms (settlement: 1 if result yes, else 0) |
   | `cash_flow` | change of cash, fee already taken off (settlement: the payout); see "Cash of a fill" |
   | `fee` | fee of the event (settlement: 0) |
   | `net_after` | position in the market after the event |
   | `cost_basis_after` | dollars paid for the position still open after the event, 0 when flat |
   | `realized_cum` | realized P&L of this run in this market up to and including the event, before fees |

4. View `fee_check`: one row per filled outcome whose stored `fee` differs from
   `kalshi_fee(quantity, price, fee_rate)` with `fee_rate = (run.params ->> 'fee_rate')::numeric`.
   Columns `run_id, outcome_id, market_ticker, quantity, price, fee, expected_fee`. Empty means
   every fee agrees. A run whose `params` has no `fee_rate` shows all its fills with
   `expected_fee` null (it cannot be checked, and says so).

Each view and each view column gets a `COMMENT ON`, and each function one, in the style of
`db/schema.sql`.

## Hard parts

Use this SQL as written (it was run by the lead against the example below). Add only the
comments.

How an event is converted:

| event | `delta` | `yes_price` |
|---|---|---|
| buy yes q @ p | +q | p |
| sell yes q @ p | −q | p |
| buy no q @ p | −q | 1 − p |
| sell no q @ p | +q | 1 − p |
| settlement of `net_units` | −net_units | 1 if result yes else 0 |

**Cash of a fill** (corrected after QA round 1). A market holds one netted position: a YES and
a NO contract of the same market cancel and return $1. So cash does not follow the verb, it
follows what the fill does to the position. With y = `yes_price`:

| what a unit of the fill does | cash per unit |
|---|---|
| opens or adds long YES | −y |
| opens or adds long NO | −(1 − y) |
| closes long YES | +y |
| closes long NO | +(1 − y) |

Example: "sell 3 yes @ 0.60" with nothing held opens 3 NO at 0.40 and costs 1.20; "buy 5 no
@ 0.70" while holding 3 YES closes the 3 YES at 0.30 (+0.90) and opens 2 NO at 0.70 (−1.40).
In the view this is written as `cash_flow = change of realized − change of cost basis − fee`,
which is the same thing. The cash of a settlement is the stored `payout`.

`pos_state.yes_cost` is the total cost of the open position in YES terms (units × YES price).
Keeping the total, not the average, keeps the books exact: cash plus cost basis always equals
starting cash plus realized P&L minus fees, with no rounding gap. For a NO position the dollars
actually paid are `abs(net) − yes_cost`.

```sql
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
```

The window order `ts, kind, seq` puts a fill before a settlement at the same instant
(`'fill' < 'settlement'`).

## Agreed tests

The journal tables are append-only, so the tests never write to the live database. The test
script creates a throwaway database `kalbt_portfolio_test` (psycopg autocommit connection,
`CREATE DATABASE`; drop it first if a previous run left it behind), applies `db/schema.sql`
then `db/portfolio.sql`, inserts the fixture, checks, and drops the database at the end. It
connects with `psycopg.connect(host="127.0.0.1", port=5433, user="kalbt", dbname=...,
password=read_password())`, `read_password` imported from `db/connect.py` (do not edit
`db/connect.py`). Output is one `[PASS]` or `[FAIL]` line per check, as in
`tests/ingest/check_load_weather_dump.py`; exit code 1 on any failure. Numbers are compared as
`Decimal`, exactly.

Fixture: underlying `NYC`, series `KXHIGHNY`, one event `EV1` on 2026-06-03, markets `A`, `B`,
`C` (open 2026-05-31 15:00 UTC, close 2026-06-04 06:00 UTC, unsettled). Run 1: `starting_cash`
1000, `params` `{"fee_rate": "0.07"}`, dates 2026-06-01 to 2026-06-04.

| # | intent (run 1) | outcome | occurred_at (UTC) |
|---|---|---|---|
| 1 | buy 10 yes A | filled 10 @ 0.40, fee 0.17 | 2026-06-01 14:01 |
| 2 | buy 5 no B | filled 5 @ 0.30, fee 0.08 | 2026-06-01 15:01 |
| 3 | sell 4 yes A | filled 4 @ 0.55, fee 0.07 | 2026-06-02 14:01 |
| 4 | buy 1 yes A | rejected, quantity 0, reason_code `no_quote` | 2026-06-02 17:01 |
| 5 | buy 1 yes A | blocked, quantity 0, reason_code `gate_locked` | 2026-06-02 18:01 |

Settlements of run 1, both at 2026-06-04 13:00 UTC: A result yes, net_units 6, payout 6;
B result yes, net_units −5, payout 0.

Row order everywhere in the tests is `ts, kind, seq, market_ticker` (the view's own order
within a market).

Expected `portfolio_ledger` rows of run 1:

| market | kind | delta | yes_price | cash_flow | fee | net_after | cost_basis_after | realized_cum |
|---|---|---|---|---|---|---|---|---|
| A | fill | 10 | 0.40 | −4.17 | 0.17 | 10 | 4.00 | 0 |
| B | fill | −5 | 0.70 | −1.58 | 0.08 | −5 | 1.50 | 0 |
| A | fill | −4 | 0.55 | 2.13 | 0.07 | 6 | 2.40 | 0.60 |
| A | settlement | −6 | 1 | 6 | 0 | 0 | 0 | 4.20 |
| B | settlement | 5 | 1 | 0 | 0 | 0 | 0 | −1.50 |

Behaviours:
1. The five rows above, exactly, and no others for run 1 (so the rejected and blocked outcomes
   produce no row).
2. Totals of run 1: `1000 + sum(cash_flow)` = 1002.38; `sum(fee)` = 0.32; the last
   `realized_cum` of A plus that of B = 2.70.
3. The book-keeping identity after every event, in `ts, seq` order: starting cash + running
   sum of `cash_flow` + sum of the latest `cost_basis_after` per market = starting cash + sum
   of the latest `realized_cum` per market − running sum of `fee`. Exact equality.
4. `kalshi_fee(12, 0.47, 0.07)` = 0.21; `kalshi_fee(10, 0.40, 0.07)` = 0.17;
   `kalshi_fee(10, 0.50, 0.04)` = 0.10 (already a whole cent, not rounded up further).
5. `fee_check` has no row for run 1.
6. Flip, in a second run (run 2, same params) on market C: buy 3 yes filled @ 0.20 fee 0.04 at
   2026-06-02 15:01, then buy 5 no filled @ 0.70 fee 0.09 at 2026-06-02 16:01. Ledger rows of
   run 2: first `delta 3, yes_price 0.20, cash_flow −0.64, net_after 3, cost_basis_after 0.60,
   realized_cum 0`; second `delta −5, yes_price 0.30, cash_flow −0.59, net_after −2,
   cost_basis_after 1.40, realized_cum 0.30`.
7. `fee_check` has exactly one row for run 2: the second fill, `fee` 0.09, `expected_fee` 0.08.
8. The runs do not mix: the run 1 rows of test 1 are unchanged after run 2 is inserted.
9. A third run with `params` `{}` and one fill appears in `fee_check` with `expected_fee` null.
10. A partial close whose cost does not divide evenly keeps the identity of test 3 exact: in a
    fourth run on market C, buy 1 yes @ 0.40 (fee 0.02), buy 2 yes @ 0.41 (fee 0.04), sell 1 yes
    @ 0.50 (fee 0.02); after the sell `net_after` = 2 and the identity holds exactly.
11. Applying `db/portfolio.sql` a second time succeeds and tests 1 and 6 still pass.
12. The live database `kalbt` has the same row counts in `run`, `order_intent`, `order_outcome`
    and `settlement` before and after the test script, and has no view `portfolio_ledger`
    created by it.

13. A sell with nothing held (fifth run, market C): sell 3 yes filled @ 0.60, fee 0.06. Row:
    `delta −3, yes_price 0.60, cash_flow −1.26, net_after −3, cost_basis_after 1.20,
    realized_cum 0`. Then the market settles no (net_units −3, payout 3): `cash_flow 3,
    net_after 0, cost_basis_after 0, realized_cum 1.80`; total cash change of the run 1.74.
14. A settlement that does not match the fills is visible: sixth run, buy 6 yes @ 0.40 (fee
    0.11), settlement with net_units 4, result yes, payout 4 → the settlement row has
    `net_after` 2 (not 0).

## Test files
- `tests/db/check_portfolio.py` (new)

## Legacy reference
- `legacy/kbt/fills.py` lines 37-40 (`fee_for`).
- `legacy/kbt/ledger.py` (cash flows and settlement).
- `legacy/kbt4/ARCHITECTURE.md`: D2 signed position, D4 average cost, D5 fees expensed at trade
  time, I3 cash conservation, I8 settlement exactness, I9 fee exactness.

## Done when
- `PYTHONPATH=. uv run python tests/db/check_portfolio.py` prints only `[PASS]` lines and exits 0.
- `uv run ruff check tests/db/check_portfolio.py` is clean.
- `git status` shows only `db/portfolio.sql`, `tests/db/check_portfolio.py` and this spec as new.

If anything here is unclear or looks wrong: stop and report, do not guess.
