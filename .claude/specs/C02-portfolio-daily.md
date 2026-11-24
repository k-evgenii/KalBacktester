# C02 — Portfolio day by day

Track: C accounting. Second unit of the approved plan (C01 ledger core, C02 day-by-day views,
C03 daily report, C04 marks and equity, C05 payoff by outcome).
STATUS: approved by the user.

## Goal
Views that show a run's book at the end of every UTC day: cash, cost of open positions,
realized P&L and fees, for the whole portfolio, per market and per underlying. A day with no
events still gets a row, and an underlying the run never traded shows 0.

## Not built
No report script (C03), no marks, equity or unrealized P&L (C04), no payoff by outcome (C05).
No new tables. `position_snapshot` is not read. `db/schema.sql` is not changed and nothing is
applied to the live database `kalbt`.

## Files
- `db/portfolio.sql` (edit: append the views below after the C01 objects, and add their drops
  to the block of drops at the top of the file, before the C01 drops; nothing else changes)

## Interfaces

Money is `numeric` dollars. A day is a UTC date: an event belongs to day `ts::date` (the
session time zone is UTC). "At the end of day D" means after every event with `ts::date <= D`.

| View | One row is | Columns |
|---|---|---|
| `portfolio_calendar` | a run and a day, from the earlier of `run.date_start` and the first event day to the later of `run.date_end` and the last event day | `run_id, day` |
| `portfolio_market_daily` | a run, a day and a market the run has traded on or before that day (flat or not) | `run_id, day, market_ticker, net_units, cost_basis, realized_cum, fees_cum, cash_cum` |
| `portfolio_position_daily` | a run, a day and a market with a non-zero position at the end of the day | `run_id, day, market_ticker, net_units, cost_basis, avg_cost` |
| `portfolio_daily` | a run and a day | `run_id, day, starting_cash, premium_paid, premium_received, fees, payouts, cash_end, cost_basis_open, realized_pnl_cum, fees_cum, net_pnl_cum, open_markets` |
| `portfolio_daily_underlying` | a run, a day and every row of `underlying` | `run_id, day, underlying_id, cost_basis_open, realized_pnl_cum, fees_cum, net_pnl_cum, open_markets` |

Column meanings:
- `premium_paid`, `premium_received`, `fees`, `payouts`: that day only. Premium is the cash of
  the day's fills before fees, split into fills that cost cash and fills that returned cash.
- `cash_end` = `starting_cash` + all `cash_flow` up to the end of the day.
- `cost_basis_open`: dollars paid for positions still open at the end of the day.
- `realized_pnl_cum`: realized P&L before fees; `fees_cum`: all fees; `net_pnl_cum` =
  `realized_pnl_cum − fees_cum`.
- `avg_cost` = `cost_basis / abs(net_units)`, rounded to 4 decimals (display only).
- `open_markets`: markets with a non-zero position at the end of the day.

Every day satisfies, exactly: `cash_end + cost_basis_open = starting_cash + net_pnl_cum`.

Each view and each view column gets a `COMMENT ON`, in the style of the C01 comments.

## Hard parts

Use this SQL as written (run by the lead against the example below). Add only the comments.

```sql
DROP VIEW IF EXISTS portfolio_daily_underlying;
DROP VIEW IF EXISTS portfolio_daily;
DROP VIEW IF EXISTS portfolio_position_daily;
DROP VIEW IF EXISTS portfolio_market_daily;
DROP VIEW IF EXISTS portfolio_calendar;

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
```

## Agreed tests

Same method as C01 (`tests/db/check_portfolio.py`): a throwaway database, `db/schema.sql` then
`db/portfolio.sql`, a hand-made fixture, exact `Decimal` comparison, the database dropped at
the end, the live database untouched. The new checks go in a new file with its own throwaway
database `kalbt_portfolio_daily_test`; the C01 test file is not changed.

Fixture: underlyings `NYC` and `CHI`; series `KXHIGHNY` on `NYC`; event `EV1` on 2026-06-03 with
markets `A` and `B`. Run 1: `starting_cash` 1000, `params` `{"fee_rate": "0.07"}`,
2026-06-01 to 2026-06-04. Run 2: `starting_cash` 500, 2026-06-01 to 2026-06-02, no intents.

| run 1 fill | occurred_at (UTC) |
|---|---|
| buy 10 yes A, filled @ 0.40, fee 0.17 | 2026-06-01 14:01:00 |
| buy 5 no B, filled @ 0.30, fee 0.08 | 2026-06-01 23:59:59 |
| sell 4 yes A, filled @ 0.55, fee 0.07 | 2026-06-02 00:00:00 |

Settlements of run 1 at 2026-06-04 13:00 UTC: A yes, net_units 6, payout 6; B yes,
net_units −5, payout 0.

Expected `portfolio_daily` for run 1:

| day | premium_paid | premium_received | fees | payouts | cash_end | cost_basis_open | realized_pnl_cum | fees_cum | net_pnl_cum | open_markets |
|---|---|---|---|---|---|---|---|---|---|---|
| 06-01 | 5.50 | 0 | 0.25 | 0 | 994.25 | 5.50 | 0 | 0.25 | −0.25 | 2 |
| 06-02 | 0 | 2.20 | 0.07 | 0 | 996.38 | 3.90 | 0.60 | 0.32 | 0.28 | 2 |
| 06-03 | 0 | 0 | 0 | 0 | 996.38 | 3.90 | 0.60 | 0.32 | 0.28 | 2 |
| 06-04 | 0 | 0 | 0 | 6 | 1002.38 | 0 | 2.70 | 0.32 | 2.38 | 0 |

Behaviours:
1. The four rows above, exactly, and no other days for run 1 (the quiet day 06-03 is there).
2. The midnight boundary: the 23:59:59 fill counts in 06-01 and the 00:00:00 fill in 06-02
   (already fixed by the table; assert it by name).
3. `cash_end + cost_basis_open = starting_cash + net_pnl_cum` on every row of `portfolio_daily`.
4. `portfolio_position_daily` for run 1: 06-01 A 10 / 4.00 / 0.4000 and B −5 / 1.50 / 0.3000;
   06-02 and 06-03 A 6 / 2.40 / 0.4000 and B −5 / 1.50 / 0.3000; no row on 06-04.
5. `portfolio_daily_underlying` for run 1: `NYC` equals the `portfolio_daily` values of
   `cost_basis_open, realized_pnl_cum, fees_cum, net_pnl_cum, open_markets` on each day; `CHI`
   has a row on each day with all five equal to 0.
6. Run 2 (no events): two rows in `portfolio_daily` (06-01, 06-02) with `cash_end` 500 and
   every other number 0; four rows in `portfolio_daily_underlying`, all 0.
7. Events outside the run's dates extend the calendar: a third run dated 2026-06-02 to
   2026-06-02 with one fill on 2026-06-01 and a settlement on 2026-06-04 has days 06-01 to 06-04.
8. Per-underlying rows sum to the portfolio: for every run and day, the sum over underlyings
   of each shared column equals the `portfolio_daily` value.
9. A second underlying that is traded: add an event on `CHI` with one market and a fourth run
   that buys in both `NYC` and `CHI`; each underlying carries only its own numbers.
10. Applying `db/portfolio.sql` twice succeeds, and `tests/db/check_portfolio.py` (C01) still
    passes unchanged.
11. The live database `kalbt` row counts are unchanged and it has no `portfolio_daily` view.

## Test files
- `tests/db/check_portfolio_daily.py` (new)

## Legacy reference
- `legacy/sql/results.sql` `run_equity` and `run_position` (the per-bar tables these views
  replace per day).
- `legacy/kbt4/ARCHITECTURE.md`: I3 cash conservation, D15 positions span days.

## Done when
- `PYTHONPATH=. uv run python tests/db/check_portfolio_daily.py` prints only `[PASS]` lines, exits 0.
- `PYTHONPATH=. uv run python tests/db/check_portfolio.py` still prints only `[PASS]` lines.
- `uvx ruff check tests/db/check_portfolio_daily.py` is clean.

If anything here is unclear or looks wrong: stop and report, do not guess.
