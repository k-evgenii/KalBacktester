"""Hourly mark-to-market equity curve of a run (spec C11).

Run from the repo root: uv run python -m portfolio.marks <run_id> [--db NAME]
Reads run, portfolio_ledger and candle. SELECT only; prints the day-end (00:00 UTC) marks and the last row.
Marks: long YES at the bid, long NO at 1 - YES ask, from the last candle at or before the hour.
"""
import argparse
import sys
from datetime import UTC, timedelta
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from db.connect import connect

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
    """Returns one dict per UTC hour of the run, oldest first (keys as in spec C11)."""
    with conn.cursor() as cur:
        cur.execute("SELECT starting_cash FROM run WHERE run_id = %s", (run_id,))
        found = cur.fetchone()
    if found is None:
        raise ValueError(f"unknown run_id {run_id}")
    starting_cash = found[0]

    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(LEDGER_SQL, (run_id,))
        ledger = cur.fetchall()
    if not ledger:
        return []
    for row in ledger:
        row["ts"] = row["ts"].astimezone(UTC)
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
        with conn.cursor() as cur:
            cur.execute(MARK_SQL, ([m for m, _ in pairs], [ts for _, ts in pairs]))
            rows = cur.fetchall()
        for market, ts, bid, ask in rows:
            quotes[(market, ts.astimezone(UTC))] = (bid, ask)

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


def print_row(row):
    print(f"{row['ts']:%Y-%m-%d %H:%M} {row['cash']:.2f} {row['positions_value']:.2f} "
          f"{row['unrealized_pnl']:.2f} {row['realized_pnl_net']:.2f} {row['equity']:.2f} "
          f"{row['open_markets']}")


def main():
    parser = argparse.ArgumentParser(description="Hourly mark-to-market equity curve of a run.")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()
    try:
        conn = connect(args.db)
        try:
            curve = equity_curve(conn, args.run_id)
        finally:
            conn.close()
    except (ValueError, psycopg.Error) as error:
        print(error)
        sys.exit(2)

    print("ts cash positions_value unrealized_pnl realized_pnl_net equity open_markets")
    for row in curve:
        if row["ts"].hour == 0:
            print_row(row)
    if curve:
        print_row(curve[-1])


if __name__ == "__main__":
    main()
