# C11 — Hourly mark-to-market equity curve of a run

Track: C accounting. Status: APPROVED (user, 2026-11-24, agreed the mark convention in chat: long YES
at the bid, long NO at 1 − YES ask, hourly marks; user away, lead proceeds with the agreed plan).

## Goal
`equity_curve(conn, run_id)` returns one row per UTC hour of a run: cash, open positions marked at the
price they could be sold at, cost basis, realized and unrealized P&L, and equity. It is the base for the
metrics (C12) and the run page charts (C13); it reads the existing journal and views and writes nothing.

## Not built
- No table, view or migration (computed on request in Python; a run is a few thousand hours at most).
- No metrics (Sharpe, drawdown, VaR: C12), no UI (C13), no per-city split (C12).
- No mid-price or last-trade marks: one convention only.

## Files
- `portfolio/marks.py` (new)

## Interfaces
```python
def equity_curve(conn, run_id) -> list[dict]
```
One dict per hour, oldest first, keys exactly:
| key | type | meaning |
|---|---|---|
| `ts` | datetime, tz UTC, on the hour | the mark time; state after every ledger row with `ts <= this` |
| `cash` | Decimal dollars | `run.starting_cash` + sum of `portfolio_ledger.cash_flow` up to `ts` |
| `positions_value` | Decimal dollars | open positions marked (rule below) |
| `cost_basis_open` | Decimal dollars | sum of `cost_basis_after` of the markets still open |
| `realized_pnl_net` | Decimal dollars | sum over markets of the last `realized_cum` minus all fees up to `ts` |
| `unrealized_pnl` | Decimal dollars | `positions_value - cost_basis_open` |
| `equity` | Decimal dollars | `cash + positions_value` |
| `open_markets` | int | markets with `net_after <> 0` at `ts` |

Hours run from `date_trunc('hour', first ledger ts)` to `date_trunc('hour', last ledger ts) + 1 hour`,
both included, every hour. A run with no ledger rows returns `[]`.

**Mark rule** (per open market at hour `h`): take the single last candle of that market with
`candle.ts <= h` (bid and ask from the same candle).
- long YES (`net > 0`): `net * yes_bid`; `yes_bid` NULL → 0.
- long NO (`net < 0`): `abs(net) * (1 - yes_ask)`; `yes_ask` NULL → 0.
- no candle at all at or before `h` → 0.
(A missing quote on the side you would sell into means nobody would buy it now.)

CLI: `uv run python -m portfolio.marks <run_id> [--db NAME]` prints one line per row whose `ts` is
00:00 UTC (the day-end marks), columns `ts cash positions_value unrealized_pnl realized_pnl_net equity
open_markets`, then the last row. SELECT only.

## Hard parts (use as written)
```python
LEDGER_SQL = """
    SELECT ts, market_ticker, cash_flow, fee, net_after, cost_basis_after, realized_cum
    FROM portfolio_ledger WHERE run_id = %s ORDER BY ts, kind, seq
"""
# one index lookup per (market, hour) on candle's primary key (market_ticker, ts)
MARK_SQL = """
    SELECT p.market_ticker, p.ts, c.yes_bid, c.yes_ask
    FROM unnest(%s::text[], %s::timestamptz[]) AS p(market_ticker, ts)
    LEFT JOIN LATERAL (
        SELECT yes_bid, yes_ask FROM candle
        WHERE market_ticker = p.market_ticker AND ts <= p.ts
        ORDER BY ts DESC LIMIT 1
    ) c ON true
"""


def equity_curve(conn, run_id):
    starting_cash = <SELECT starting_cash FROM run WHERE run_id = %s>
    ledger = <fetch LEDGER_SQL as dicts>
    if not ledger:
        return []
    first = ledger[0]["ts"].replace(minute=0, second=0, microsecond=0)
    last = ledger[-1]["ts"].replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)

    # walk the hours and the ledger together
    cash, fees, i = starting_cash, Decimal(0), 0
    state = {}       # market -> (net_after, cost_basis_after, realized_cum) of its last ledger row
    hours = []       # (ts, cash, fees, {market: state} copy)
    h = first
    while h <= last:
        while i < len(ledger) and ledger[i]["ts"] <= h:
            row = ledger[i]
            cash += row["cash_flow"]
            fees += row["fee"]
            state[row["market_ticker"]] = (row["net_after"], row["cost_basis_after"], row["realized_cum"])
            i += 1
        hours.append((h, cash, fees, dict(state)))
        h += timedelta(hours=1)

    # one query for every (open market, hour) quote
    pairs = [(m, ts) for ts, _, _, st in hours for m, s in st.items() if s[0] != 0]
    quotes = {}      # (market, ts) -> (yes_bid, yes_ask)
    if pairs:
        <execute MARK_SQL with ([m for m, _ in pairs], [ts for _, ts in pairs])>
        for market, ts, bid, ask in rows:
            quotes[(market, ts)] = (bid, ask)

    result = []
    for ts, cash, fees, st in hours:
        value, cost, realized, n_open = Decimal(0), Decimal(0), Decimal(0), 0
        for market, (net, cost_basis, realized_cum) in st.items():
            realized += realized_cum
            if net == 0:
                continue
            n_open += 1
            cost += cost_basis
            bid, ask = quotes[(market, ts)]
            if net > 0:
                value += net * bid if bid is not None else 0
            else:
                value += -net * (1 - ask) if ask is not None else 0
        result.append({"ts": ts, "cash": cash, "positions_value": value, "cost_basis_open": cost,
                       "realized_pnl_net": realized - fees, "unrealized_pnl": value - cost,
                       "equity": cash + value, "open_markets": n_open})
    return result
```
(`<...>` lines: write them plainly with psycopg; return timestamps as tz-aware UTC. Convert any `ts`
psycopg returns for the hours to UTC before using it as a dict key so lookups match.)

## Agreed tests (`tests/portfolio/check_marks.py`, qa-tester)
Throwaway database `kalbt_marks_test_<pid>` built from `db/schema.sql` + `db/portfolio.sql`, loaded
with `load_fixture` from `tests/recon/check_check_run.py`, dropped first and at the end (also on
failure). Build the run with that file's `hand_run` (starting cash 1000):
- buy YES `T-M1` 5, decided 2026-05-31 16:00 UTC, filled 5 @ 0.45, fee 0.09, at 2026-05-31 16:01;
- buy NO `T-M2` 10, decided 2026-06-01 02:00, filled 10 @ 0.55, fee 0.18, at 2026-06-01 02:01;
- settlements (at the fixture's SETTLED_AT 2026-06-02 12:00): `T-M1` yes, net 5, payout 5;
  `T-M2` no, net −10, payout 10.
Then insert two extra candles: `T-M1` 2026-06-01 22:00 bid NULL ask 0.60 volume 0;
`T-M2` 2026-06-02 01:30 bid 0.98 ask NULL volume 0.

Expected (hand values, Decimal compare after rounding to 2 places):
1. 46 rows, first `ts` 2026-05-31 16:00 UTC, last 2026-06-02 13:00 UTC, consecutive hours.
2. 2026-05-31 16:00: cash 1000, positions_value 0, equity 1000, open_markets 0.
3. 2026-05-31 17:00: cash 997.66, positions_value 2.00, cost_basis_open 2.25, unrealized −0.25,
   realized_pnl_net −0.09, equity 999.66, open_markets 1.
4. 2026-06-01 03:00: cash 991.98, positions_value 7.30 (2.00 + 10 × 0.53), cost_basis_open 7.75,
   unrealized −0.45, realized_pnl_net −0.27, equity 999.28, open_markets 2.
5. 2026-06-01 11:00: positions_value 7.50 (M1 at the 10:11 bid 0.44), equity 999.48.
6. 2026-06-01 21:00: positions_value 7.80 (M1 at the 20:00 bid 0.50), equity 999.78.
7. 2026-06-01 23:00: M1 NULL bid → 0: positions_value 5.30, equity 997.28.
8. 2026-06-02 02:00: M2 NULL ask → 0 as well: positions_value 0, equity 991.98, open_markets 2.
9. 2026-06-02 12:00 and 13:00: cash 1006.98, positions_value 0, cost_basis_open 0,
   realized_pnl_net 6.98, equity 1006.98, open_markets 0.
10. Every row: `cash + cost_basis_open == 1000 + realized_pnl_net` exactly, and
    `equity == 1000 + realized_pnl_net + unrealized_pnl` exactly.
11. A run with no fills (hand_run with no entries) → `[]`.
12. Live, read-only: `equity_curve(connect("kalbt"), 32)` finishes in under 10 s; its last row has
    `open_markets == 0` and `equity == 1000 + net_pnl_cum` of run 32's last `portfolio_daily` row.
    Row counts of `run`, `order_intent`, `order_outcome` in kalbt are unchanged.
13. CLI on run 32 exits 0 and prints at least one line.

## Test files
- `tests/portfolio/check_marks.py`

## Legacy reference
`legacy/kbt/metrics.py` (intraday mark-to-market drawdown idea only; the legacy marks are not ported).

## Done when
- `PYTHONPATH=. uv run python tests/portfolio/check_marks.py` exits 0
- `uv run ruff check portfolio/marks.py tests/portfolio/check_marks.py` is clean
- `uv run python -m portfolio.marks 32` prints the day-end lines

If anything here is unclear or looks wrong: stop and report, do not guess.
