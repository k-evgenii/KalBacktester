"""Run metrics: trades, daily P&L, Sharpe, drawdown, VaR, event risk, exposure (spec C12).

Run from the repo root: uv run python -m portfolio.metrics <run_id> [--db NAME]
Builds on equity_curve (C11). SELECT only; prints one `key value` line per metric.
"""
import argparse
import math
import statistics
import sys
from datetime import timedelta
from decimal import Decimal
from itertools import pairwise

import psycopg
from psycopg.rows import dict_row

from db.connect import connect
from portfolio.marks import equity_curve

TRADES_SQL = """
    SELECT l.market_ticker, k.event_ticker, e.event_date, e.underlying_id,
           min(l.ts) AS first_ts, max(l.ts) AS last_ts,
           count(*) FILTER (WHERE l.kind = 'fill') AS n_fills,
           sum(l.fee) AS fees,
           (array_agg(l.realized_cum ORDER BY l.ts DESC, l.kind DESC, l.seq DESC))[1] - sum(l.fee) AS pnl_net,
           (array_agg(l.net_after ORDER BY l.ts DESC, l.kind DESC, l.seq DESC))[1] <> 0 AS open
    FROM portfolio_ledger l
    JOIN market k ON k.ticker = l.market_ticker
    JOIN event e ON e.ticker = k.event_ticker
    WHERE l.run_id = %s
    GROUP BY l.market_ticker, k.event_ticker, e.event_date, e.underlying_id
    ORDER BY min(l.ts), l.market_ticker
"""
EVENT_LEDGER_SQL = """
    SELECT l.market_ticker, k.event_ticker, l.net_after, l.cost_basis_after
    FROM portfolio_ledger l JOIN market k ON k.ticker = l.market_ticker
    WHERE l.run_id = %s ORDER BY l.ts, l.kind, l.seq
"""
EVENT_MARKETS_SQL = """
    SELECT k.ticker, k.event_ticker, e.underlying_id, e.event_date
    FROM market k JOIN event e ON e.ticker = k.event_ticker
    WHERE k.event_ticker = ANY(%s)
"""


def trades(conn, run_id):
    """One row per market the run has a ledger row in, ordered by first_ts."""
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(TRADES_SQL, (run_id,))
        return cur.fetchall()


def daily_points(curve):
    """Start point, every 00:00 UTC mark after it, and the last row; each with the P&L since the
    previous point. 'day' is the UTC day that ended at that point."""
    if not curve:
        return []
    points = [curve[0]] + [r for r in curve[1:] if r["ts"].hour == 0]
    if points[-1] is not curve[-1]:
        points.append(curve[-1])
    out = []
    for prev, cur in pairwise(points):
        day = (cur["ts"] - timedelta(microseconds=1)).date() if cur["ts"].hour == 0 else cur["ts"].date()
        pnl = cur["equity"] - prev["equity"]
        out.append({"day": day, "ts": cur["ts"], "equity": cur["equity"], "pnl": pnl,
                    "ret": float(pnl / prev["equity"])})
    return out


def historical_var(pnls, level):
    """Loss not exceeded on `level` of days: the k-th worst day, k = floor((1 - level) * n), no interpolation."""
    ordered = sorted(pnls)
    k = math.floor((1 - level) * len(ordered) + 1e-9)
    return max(Decimal(0), -ordered[k])


def worst_loss(markets, nets, costs):
    payouts = []
    for winner in markets:
        pay = Decimal(0)
        for m, net in nets.items():
            if net > 0 and m == winner:
                pay += net
            elif net < 0 and m != winner:
                pay += -net
        payouts.append(pay)
    return sum(costs.values(), Decimal(0)) - min(payouts)


def event_risk(conn, run_id):
    """Returns (rows, peak_capital_at_risk): one row per event traded, and the largest total
    worst-case loss over all events' open positions after any ledger row."""
    with conn.cursor() as cur:
        cur.execute(EVENT_LEDGER_SQL, (run_id,))
        ledger_rows = cur.fetchall()
    events = sorted({row[1] for row in ledger_rows})
    markets_of = {}     # event -> every market ticker of it
    info = {}           # event -> (underlying_id, event_date)
    with conn.cursor() as cur:
        cur.execute(EVENT_MARKETS_SQL, (events,))
        for ticker, event, underlying_id, event_date in cur.fetchall():
            markets_of.setdefault(event, []).append(ticker)
            info[event] = (underlying_id, event_date)

    nets, costs = {}, {}          # event -> {market: value}, only open markets
    current = {}                  # event -> its worst loss now
    worst = {}                    # event -> max worst loss seen
    peak = Decimal(0)
    for market, event, net, cost in ledger_rows:
        nets.setdefault(event, {}).pop(market, None)
        costs.setdefault(event, {}).pop(market, None)
        if net != 0:
            nets[event][market] = net
            costs[event][market] = cost
        current[event] = worst_loss(markets_of[event], nets[event], costs[event]) if nets[event] else Decimal(0)
        worst[event] = max(worst.get(event, Decimal(0)), current[event])
        peak = max(peak, sum(current.values(), Decimal(0)))
    rows = [{"event_ticker": e, "underlying_id": info[e][0], "event_date": info[e][1],
             "max_worst_loss": worst[e]}
            for e in sorted(worst)]
    return rows, peak


def max_drawdown(curve):
    """Returns (drop, drop / peak, trough ts); (0, 0.0, None) when equity never falls below its peak."""
    peak = curve[0]["equity"]
    best_drop, best_peak, best_ts = Decimal(0), None, None
    for row in curve:
        peak = max(peak, row["equity"])
        drop = peak - row["equity"]
        if drop > best_drop:
            best_drop, best_peak, best_ts = drop, peak, row["ts"]
    if best_drop == 0:
        return Decimal(0), 0.0, None
    return best_drop, float(best_drop / best_peak), best_ts


def mean_over_stdev(values):
    """Sample mean / sample stdev of floats; None with fewer than 2 values or zero spread."""
    if len(values) < 2:
        return None
    spread = statistics.stdev(values)
    if spread == 0:
        return None
    return statistics.mean(values) / spread


def metrics(curve, trade_rows, peak_capital_at_risk, max_event_worst_loss):
    out = dict.fromkeys([
        "starting_cash", "final_equity", "total_pnl", "total_return",
        "max_drawdown", "max_drawdown_pct", "max_drawdown_at", "sharpe_daily", "var_95", "var_99",
        "win_rate", "avg_win", "avg_loss", "profit_factor", "expectancy", "sharpe_per_trade",
        "max_cost_basis_open", "time_in_market"])
    daily = daily_points(curve)
    closed = [t["pnl_net"] for t in trade_rows if not t["open"]]
    out.update({
        "n_days": len(daily),
        "n_trades": len(trade_rows),
        "n_open": sum(1 for t in trade_rows if t["open"]),
        "fees": sum((t["fees"] for t in trade_rows), Decimal(0)),
        "max_open_markets": 0,
        "peak_capital_at_risk": peak_capital_at_risk,
        "max_event_worst_loss": max_event_worst_loss,
    })

    if curve:
        out["starting_cash"] = curve[0]["equity"]
        out["final_equity"] = curve[-1]["equity"]
        out["total_pnl"] = out["final_equity"] - out["starting_cash"]
        out["total_return"] = float(out["total_pnl"] / out["starting_cash"])
        out["max_drawdown"], out["max_drawdown_pct"], out["max_drawdown_at"] = max_drawdown(curve)
        out["max_cost_basis_open"] = max(r["cost_basis_open"] for r in curve)
        out["max_open_markets"] = max(r["open_markets"] for r in curve)
        out["time_in_market"] = sum(1 for r in curve if r["open_markets"] > 0) / len(curve)

    if daily:
        pnls = [d["pnl"] for d in daily]
        out["var_95"] = historical_var(pnls, 0.95)
        out["var_99"] = historical_var(pnls, 0.99)
        out["sharpe_daily"] = mean_over_stdev([d["ret"] for d in daily])
        if out["sharpe_daily"] is not None:
            out["sharpe_daily"] *= math.sqrt(365)

    if closed:
        wins = [p for p in closed if p > 0]
        losses = [p for p in closed if p < 0]
        out["win_rate"] = len(wins) / len(closed)
        out["avg_win"] = sum(wins, Decimal(0)) / len(wins) if wins else None
        out["avg_loss"] = sum(losses, Decimal(0)) / len(losses) if losses else None
        if losses:
            out["profit_factor"] = sum(wins, Decimal(0)) / -sum(losses, Decimal(0))
        out["expectancy"] = sum(closed, Decimal(0)) / len(closed)
        out["sharpe_per_trade"] = mean_over_stdev([float(p) for p in closed])
    return out


def run_metrics(conn, run_id):
    curve = equity_curve(conn, run_id)
    trade_rows = trades(conn, run_id)
    event_rows, peak = event_risk(conn, run_id)
    max_event = max((r["max_worst_loss"] for r in event_rows), default=Decimal(0))
    return metrics(curve, trade_rows, peak, max_event)


def main():
    parser = argparse.ArgumentParser(description="Headline metrics of a run.")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()
    try:
        conn = connect(args.db)
        try:
            result = run_metrics(conn, args.run_id)
        finally:
            conn.close()
    except (ValueError, psycopg.Error) as error:
        print(error)
        sys.exit(2)

    for key, value in result.items():
        print(key, value)


if __name__ == "__main__":
    main()
