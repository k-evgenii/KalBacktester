# Track C note for the lead chat and Track B

From the Track C chat (branch `track-c`, folder `../KalBacktester-track-c`).

## For the lead chat (STATUS.md)
- Track C units are numbered C01, C02, ... (specs `.claude/specs/C01-*.md`).
- `track-c` contains `track-b` up to `9c0cb05` (unit 011 views).
- C01 "portfolio ledger core" is built and approved by the reviewer; not committed yet.
- `reconcile.py` is dropped from Track C: Track B's 013 owns reconciliation.
- Track C adds `db/portfolio.sql` (functions and views only, applied after `db/schema.sql`).
  It does not edit `db/schema.sql`. It is not applied to the live database yet.

## For Track B (unit 012, matching engine)
The portfolio recomputes positions, cost and P&L from `order_outcome` (filled) and
`settlement` only. It relies on three things from the engine:
1. `run.params` has `fee_rate` (text or number). View `fee_check` compares every stored fee
   with `ceil(fee_rate * quantity * price * (1 - price) * 100) / 100`.
2. `settlement.net_units` equals the net of the run's fills in that market, and `payout` is
   the units on the winning side. A mismatch shows as a non-zero position after settlement.
3. One netted position per market: buying NO while holding YES closes the YES (cash follows
   the netted position, not the verb). If the engine only allows sells up to the holding,
   nothing changes for it.
`position_snapshot` is not read by the portfolio; it can be compared with the view
`portfolio_position_daily` (C02, same UTC day boundary) in the reconciliation unit.
