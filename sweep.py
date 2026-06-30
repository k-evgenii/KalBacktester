#!/usr/bin/env python3
"""
sweep.py — grid a fade strategy over HOURS-BEFORE-CLOSE x CONVICTION on PnL.

Every cell is a real backtest (build_event_days -> run_event -> settle ->
compute_metrics), gate and all. Works on two strategies; the display adapts:

  --strategy target  (default) -> kbt.strategies.midday_fade.MiddayFadeAt
      Enter AT exactly H hours before close, if the favourite's YES >= conviction
      at that bar. Each H is a distinct entry bar, so the grid is a clean
      optimal-entry surface. Far-out hours show empty rows (no bar / market not
      open at that hour) rather than phantom fills. Display focuses on the
      optimisation: PnL/trade grids, best cell, top cells, entry-hour profile.

  --strategy armed -> kbt.strategies.armed_fade.ArmedFade
      Arm at H hours before close, fade on the FIRST bar where conviction crosses.
      The entry time is data-driven, so high-H rows plateau (slack start gate).
      Display focuses on diagnostics: distinct fills with POST-MAX flags and the
      late-entry block check.

The hour axis is HARDCODED to 0..48 (1h steps). Conviction (min_yes) is a CLI
range "start:stop:step" (stop inclusive).

Example:
    python sweep.py --data ./data --city new_york --series KXHIGHNY \
        --strategy target --conv 0.50:0.95:0.05 --out ./out/sweep.csv
"""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

from kbt.engine import run_event
from kbt.ledger import Ledger
from kbt.metrics import compute_metrics
from kbt.run import build_event_days
from kbt.strategies.midday_fade import MiddayFadeAt
from kbt.strategies.armed_fade import ArmedFade

HOURS = list(range(0, 49))   # hardcoded entry-time axis, 1h steps

STRATEGIES = {
    "target": {"class": MiddayFadeAt, "mode": "target",
               "label": "MiddayFadeAt — enter AT H hours before close"},
    "armed":  {"class": ArmedFade, "mode": "armed",
               "label": "ArmedFade — arm at H, fade on first conviction crossing"},
}


def parse_range(spec: str) -> list[float]:
    a, b, step = (float(x) for x in spec.split(":"))
    n = int(round((b - a) / step)) + 1
    out = [round(a + i * step, 6) for i in range(max(n, 1))]
    return [x for x in out if x <= b + 1e-9]


def _parse_iso(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None


def _close_time(day):
    for s in getattr(day, "specs", []):
        if getattr(s, "close_time", None) is not None:
            return s.close_time
    return None


def _hours_before_close(ts, close):
    if ts is None or close is None:
        return None
    return (close - ts).total_seconds() / 3600.0


def run_cell(strat_cls, days, h_before_close, conv, qty, slippage):
    """One full backtest for a grid cell. Returns (metrics, trade_details)."""
    strat = strat_cls(hours_before_close=h_before_close, min_yes=conv, quantity=qty)
    ledger = Ledger()
    all_trades, trade_details = [], []
    for day in days:
        trades = run_event(strat, day, ledger, slippage=slippage)
        if trades:
            close = _close_time(day)
            max_time = day.daily_max.max_time_utc if day.daily_max else None
            for t in trades:
                trade_details.append({
                    "event_date": day.event_date_local,
                    "entry_ts": t.ts,
                    "ticker": t.ticker,
                    "max_time_utc": max_time,
                    "entry_h_before_close": _hours_before_close(t.ts, close),
                    "max_h_before_close": _hours_before_close(_parse_iso(max_time), close),
                })
        all_trades.extend(trades)
    return compute_metrics(all_trades, ledger.equity_curve), trade_details


# --------------------------------------------------------------------------
# shared grid printing
# --------------------------------------------------------------------------
def _print_grids(grid, hours_desc, convs):
    header = "  Hbc\\conv |" + "".join(f"{c:>8.2f}" for c in convs)
    print("PnL grid  ($ total; rows = hours before close, cols = conviction floor)")
    print(header); print("  " + "-" * (len(header) - 2))
    for h in hours_desc:
        print(f"  {h:>7g} |" + "".join(f"{grid[(h, c)][0].total_pnl:>8.2f}" for c in convs))
    print("\nTrade-count grid  (same axes)")
    print(header); print("  " + "-" * (len(header) - 2))
    for h in hours_desc:
        print(f"  {h:>7g} |" + "".join(f"{grid[(h, c)][0].n_trades:>8d}" for c in convs))


def _best_and_csv(grid, hours_desc, convs, min_trades, out):
    eligible = {k: m for k, m in grid.items() if m[0].n_trades >= min_trades}
    print("\n" + "=" * 52)
    if not eligible:
        print(f"  No cell reached --min-trades={min_trades}; nothing to optimise.")
    else:
        (bh, bc), (bm, _) = max(eligible.items(), key=lambda kv: kv[1][0].total_pnl)
        print(f"  BEST  hours_before_close={bh:g}  conviction={bc:.2f}  "
              f"(among cells with >={min_trades} trades)")
        print(f"  PnL ${bm.total_pnl:+.2f}  trades={bm.n_trades}  win={bm.win_rate:.1%}  "
              f"sharpe={bm.sharpe_per_trade:.2f}  maxDD ${bm.max_drawdown:.2f}")
        if bm.total_pnl <= 0:
            print("  ! Best cell is still <= $0 — unprofitable across the grid.")
    print("=" * 52)
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["hours_before_close", "conviction", "n_trades", "total_pnl",
                        "win_rate", "profit_factor", "sharpe_per_trade",
                        "max_drawdown", "max_drawdown_intraday"])
            for h in hours_desc:
                for c in convs:
                    m = grid[(h, c)][0]
                    w.writerow([h, c, m.n_trades, round(m.total_pnl, 4),
                                round(m.win_rate, 4), round(m.profit_factor, 4),
                                round(m.sharpe_per_trade, 4), round(m.max_drawdown, 4),
                                round(m.max_drawdown_intraday, 4)])
        print(f"\nWrote grid to {out}")
    return eligible


# --------------------------------------------------------------------------
# mode-specific extras
# --------------------------------------------------------------------------
def _display_target(grid, hours_desc, convs, eligible):
    """Optimisation view: top cells + entry-hour profile at the best conviction."""
    print("\nEmpty high-H rows mean no bar exists at that target hour (market not")
    print("open / no candle that early) — not a phantom fill.")

    if eligible:
        top = sorted(eligible.items(), key=lambda kv: kv[1][0].total_pnl, reverse=True)[:5]
        print("\nTop 5 (hour, conviction) cells by PnL:")
        for (h, c), (m, _) in top:
            print(f"    H={h:>3g}  conv={c:.2f}  ->  PnL ${m.total_pnl:+.2f}  "
                  f"trades={m.n_trades}  win={m.win_rate:.1%}  sharpe={m.sharpe_per_trade:.2f}")
        # entry-hour profile at the best conviction column
        best_c = max(convs, key=lambda c: sum(grid[(h, c)][0].total_pnl for h in hours_desc))
        print(f"\nEntry-hour profile at conviction={best_c:.2f} "
              f"(PnL / trades by exact entry hour):")
        for h in hours_desc:
            m = grid[(h, best_c)][0]
            if m.n_trades:
                print(f"    {h:>3g}h before close -> ${m.total_pnl:+6.2f}  ({m.n_trades} trades)")


def _display_armed(grid, hours_desc, convs, n_total, n_blocked_days):
    """Diagnostic view: distinct fills with POST-MAX flags + late-entry check."""
    print("\n" + "=" * 72)
    print("INSPECTION: distinct fills, latest-first  (validate post-max entries)")
    print("  POST-MAX = fill landed after the high printed (nearer close than max).")
    print("=" * 72)
    seen = {}
    for (_h, _c), (_m, details) in grid.items():
        for d in details:
            seen.setdefault((d["event_date"], d["ticker"], d["entry_ts"]), d)
    fills = sorted(seen.values(),
                   key=lambda d: (d["entry_h_before_close"]
                                  if d["entry_h_before_close"] is not None else 1e9))
    post_max = [d for d in fills
                if d["entry_h_before_close"] is not None and d["max_h_before_close"] is not None
                and d["entry_h_before_close"] < d["max_h_before_close"]]
    print(f"\n  {len(fills)} distinct fills total; {len(post_max)} are POST-MAX.\n")
    for d in fills[:15]:
        eh, mh = d["entry_h_before_close"], d["max_h_before_close"]
        eh_s = f"{eh:4.1f}h" if eh is not None else "  ? "
        mh_s = f"{mh:4.1f}h" if mh is not None else "  ? "
        flag = "  <== POST-MAX" if (eh is not None and mh is not None and eh < mh) else ""
        print(f"  {d['event_date']}  {d['ticker']}")
        print(f"      entry@ {d['entry_ts']}  ({eh_s} before close)")
        print(f"      max@   {d['max_time_utc']}  ({mh_s} before close){flag}")
    print("=" * 72)

    c0 = convs[0]
    print(f"\nLate-entry block check (conviction={c0:.2f}, most permissive):")
    for h in hours_desc:
        print(f"    {h:>3g}h before close -> {grid[(h, c0)][0].n_trades:3d} trades")
    print(f"  ({n_blocked_days}/{n_total} event-days hard-BLOCKED, excluded at every hour.)")


def main() -> None:
    ap = argparse.ArgumentParser(description="hours-before-close x conviction PnL grid.")
    ap.add_argument("--data", type=Path, default=Path("data"))
    ap.add_argument("--city", required=True)
    ap.add_argument("--series", required=True)
    ap.add_argument("--strategy", choices=list(STRATEGIES), default="target",
                    help="target = enter AT hour H (benchmark); armed = fade on first crossing")
    ap.add_argument("--conv", default="0.50:0.95:0.05", help="start:stop:step (min_yes)")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--allow-post-max", action="store_true",
                    help="disable the blanket time-stop (default OFF — hard rule ON)")
    ap.add_argument("--out", type=Path, default=None, help="write the full grid CSV here")
    ap.add_argument("--min-trades", type=int, default=5,
                    help="min trades for a cell to be eligible as 'best'")
    args = ap.parse_args()

    spec = STRATEGIES[args.strategy]
    strat_cls, mode = spec["class"], spec["mode"]
    convs = parse_range(args.conv)
    hours_desc = sorted(HOURS, reverse=True)

    days = build_event_days(args.data, args.city, args.series,
                            blanket_time_stop=not args.allow_post_max)
    if not days:
        print("No event-days found. Check --data / --city / --series.")
        return
    n_total = len(days)
    n_blocked_days = sum(1 for d in days if getattr(d.gate, "blocked", False))

    print(f"\nStrategy: {spec['label']}")
    print(f"Event-days: {n_total}  ({n_blocked_days} BLOCKED, {n_total - n_blocked_days} tradeable)")
    print(f"Grid: {len(HOURS)} hours x {len(convs)} conviction = {len(HOURS)*len(convs)} backtests\n")

    grid = {}
    for h in HOURS:
        for c in convs:
            grid[(h, c)] = run_cell(strat_cls, days, h, c, args.qty, args.slippage)

    _print_grids(grid, hours_desc, convs)
    eligible = _best_and_csv(grid, hours_desc, convs, args.min_trades, args.out)

    if mode == "target":
        _display_target(grid, hours_desc, convs, eligible)
    else:
        _display_armed(grid, hours_desc, convs, n_total, n_blocked_days)


if __name__ == "__main__":
    main()