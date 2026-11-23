"""
run.py — end-to-end backtest CLI.

    python -m kbt.run --data ./data --city new_york --series KXHIGHNY
    python -m kbt.run --data ./data --city new_york --series KXHIGHNY --out ./out

Pipeline:
    load markets → group into event-days → load bars + obs per day →
    run engine → settle from Kalshi result → metrics → print + (optional) write.

Writes (with --out):
    metrics.json   the summary stats
    trades.csv     every trade with settlement + PnL
    equity.csv     the equity curve
"""
from __future__ import annotations

import argparse
import csv
import json
import logging
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from .contracts import spec_from_market_dict
from .engine import EventDay, run_event
from .ledger import Ledger
from .loaders import load_bars, load_markets, load_weather
from .metrics import compute_metrics, format_report
from .schemas import Trade

# default NWS station per city (extend as needed)
STATION = {"new_york": "KNYC"}


def _event_date_str(event_ticker: str) -> str:
    # KXHIGHNY-26MAY14 -> "26MAY14"
    return event_ticker.split("-")[-1]


def _event_date_iso(event_ticker: str) -> str | None:
    """26MAY14 -> 2026-05-14 (for matching NWS obs filenames)."""
    import re
    m = re.search(r"(\d{2})([A-Z]{3})(\d{2})$", event_ticker)
    if not m:
        return None
    yy, mon, dd = m.groups()
    months = {"JAN":"01","FEB":"02","MAR":"03","APR":"04","MAY":"05","JUN":"06",
              "JUL":"07","AUG":"08","SEP":"09","OCT":"10","NOV":"11","DEC":"12"}
    if mon not in months:
        return None
    return f"20{yy}-{months[mon]}-{dd}"


def build_event_days(data: Path, city: str, series: str,
                     blanket_time_stop: bool = True) -> list[EventDay]:
    markets_dir = data / "raw" / "kalshi" / city / "markets"
    candles_dir = data / "raw" / "kalshi" / city / "candles"
    processed   = data / "processed"

    raw_markets = load_markets(markets_dir, series)
    by_event: dict[str, list[dict]] = defaultdict(list)
    for m in raw_markets:
        by_event[m.get("event_ticker", "")].append(m)

    # official daily-max cache (CLI via IEM); missing -> {} -> all days block
    weather = load_weather(processed, city)
    n_blocked = 0

    days: list[EventDay] = []
    for event_ticker, mkts in sorted(by_event.items()):
        if not event_ticker:
            continue
        specs = [spec_from_market_dict(m) for m in mkts]
        # only keep contracts we actually have candles for
        bars_by_ticker = {}
        kept_specs = []
        for s in specs:
            bars = load_bars(candles_dir, s.ticker)
            if bars:
                bars_by_ticker[s.ticker] = bars
                kept_specs.append(s)
        if not kept_specs:
            continue

        iso = _event_date_iso(event_ticker)
        from datetime import date
        ev_date = date.fromisoformat(iso) if iso else date(2000, 1, 1)

        daily_max = weather.get(iso)
        if daily_max is None or daily_max.status != "OK":
            n_blocked += 1

        days.append(EventDay(
            city_key=city,
            event_date_local=ev_date,
            specs=kept_specs,
            bars_by_ticker=bars_by_ticker,
            daily_max=daily_max,
            blanket_time_stop=blanket_time_stop,
        ))

    if n_blocked:
        print(f"[weather] {n_blocked}/{len(days)} event-days BLOCKED "
              f"(no trusted daily max) for {city}/{series}")
    return days


def main() -> None:
    ap = argparse.ArgumentParser(description="Kalshi weather backtester")
    ap.add_argument("--data", type=Path, required=True, help="path to data/ dir")
    ap.add_argument("--city", default="new_york")
    ap.add_argument("--series", default="KXHIGHNY")
    ap.add_argument("--qty", type=int, default=1)
    ap.add_argument("--slippage", type=float, default=0.0)
    ap.add_argument("--strategy", default=None,
                    help="path to a strategy .py file, or a built-in stem "
                         "(e.g. highest_temp). If omitted, uses the built-in "
                         "highest_temp default.")
    ap.add_argument("--allow-post-max", action="store_true",
                    help="disable the blanket time-stop (allow entries at/after "
                         "the max bar). Off by default — the hard rule is ON.")
    ap.add_argument("--out", type=Path, default=None, help="write metrics/trades/equity here")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    from .strategy_loader import load_strategy
    if args.strategy:
        strategy = load_strategy(args.strategy)
    else:
        # default for out-of-the-box use; lazily loaded so the core run path
        # imports no concrete strategy at module level.
        strategy = load_strategy("highest_temp")
        if hasattr(strategy, "quantity"):
            try:
                strategy.quantity = args.qty
            except Exception:
                pass
    ledger = Ledger()
    all_trades: list[Trade] = []

    days = build_event_days(args.data, args.city, args.series,
                            blanket_time_stop=not args.allow_post_max)
    if not days:
        print("No event-days found. Check --data / --city / --series.")
        return

    for day in days:
        trades = run_event(strategy, day, ledger, slippage=args.slippage)
        all_trades.extend(trades)

    metrics = compute_metrics(all_trades, ledger.equity_curve)
    print(f"\nEvent-days: {len(days)}   Series: {args.series}   City: {args.city}")
    print(format_report(metrics, strategy.name))

    if args.out:
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "metrics.json").write_text(json.dumps(
            {"strategy": strategy.name, "params": strategy.params, **metrics.as_dict()},
            indent=2,
        ))
        with (args.out / "trades.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts","event_date","ticker","action","qty","fill_price","fee","result","pnl","reason"])
            for t in all_trades:
                w.writerow([t.ts.isoformat(), t.event_date_local, t.ticker, t.action,
                            t.quantity, f"{t.fill_price:.4f}", f"{t.fee:.4f}",
                            t.result or "", "" if t.pnl is None else f"{t.pnl:.4f}", t.reason])
        with (args.out / "equity.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["event_date", "cum_pnl"])      # one clean point per settled day
            for label, eq in ledger.realized_curve:
                w.writerow([label, f"{eq:.4f}"])
        with (args.out / "equity_intraday.csv").open("w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["ts", "mtm"])                   # raw mark-to-market (overlapping)
            for ts, eq in ledger.equity_curve:
                w.writerow([ts.isoformat(), f"{eq:.4f}"])
        print(f"\nWrote metrics.json, trades.csv, equity.csv, equity_intraday.csv to {args.out}/")


if __name__ == "__main__":
    main()