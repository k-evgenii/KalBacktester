"""Benchmark strategy: buy NO on the day's favourite (highest YES mid) some hours before close.

Run from the repo root:
    uv run python -m strategies.midday_fade --series KXHIGHNY --start 2026-11-16 \
        --end 2026-11-22 [--db NAME]
Rules are in .claude/specs/B021-midday-fade-strategy.md. SELECT only.
"""
import argparse
from datetime import UTC, date, timedelta
from decimal import Decimal

from db.connect import connect

DEFAULTS = {"hours_before_close": 15, "min_yes": "0.50", "max_yes": "0.95", "quantity": 10}

QUOTES_SQL = """
    SELECT m.ticker, c.yes_bid, c.yes_ask
    FROM market m
    JOIN LATERAL (
        SELECT yes_bid, yes_ask FROM candle
        WHERE market_ticker = m.ticker AND ts <= %(t)s AND ts > %(t)s - interval '1 hour'
        ORDER BY ts DESC LIMIT 1
    ) c ON true
    WHERE m.event_ticker = %(e)s
    ORDER BY m.ticker
"""


def decision_time(close_time, hours_before_close):
    """Close rounded UP to the hour (the settlement hour), minus H hours (legacy _target_ts)."""
    anchor = close_time.replace(minute=0, second=0, microsecond=0)
    if close_time != anchor:
        anchor += timedelta(hours=1)
    return anchor - timedelta(hours=hours_before_close)


def intents_for_event(conn, event_ticker, params):
    """Return (intents, info): 0 or 1 intent in the engine's format, and a summary dict."""
    info = {"event_ticker": event_ticker, "decided_at": None, "favourite_market": None,
            "favourite_mid": None, "skipped": None}
    with conn.cursor() as cur:
        cur.execute("SELECT max(close_time) FROM market WHERE event_ticker = %s", (event_ticker,))
        close_time = cur.fetchone()[0]
        if close_time is None:
            info["skipped"] = "no_quotes"
            return [], info
        decided_at = decision_time(close_time.astimezone(UTC), params["hours_before_close"])
        info["decided_at"] = decided_at
        cur.execute(QUOTES_SQL, {"t": decided_at, "e": event_ticker})
        rows = cur.fetchall()

    min_yes, max_yes = Decimal(params["min_yes"]), Decimal(params["max_yes"])
    quoted = [(ticker, (bid + ask) / 2) for ticker, bid, ask in rows if bid is not None and ask is not None]
    if not quoted:
        info["skipped"] = "no_quotes"
        return [], info
    best = None
    for ticker, mid in quoted:                 # ordered by ticker: ties keep the first ticker
        if min_yes <= mid <= max_yes and (best is None or mid > best[1]):
            best = (ticker, mid)
    if best is None:
        info["skipped"] = "no_favourite"
        return [], info
    info["favourite_market"], info["favourite_mid"] = best
    intent = {"decided_at": decided_at, "market_ticker": best[0], "side": "no", "action": "buy",
              "quantity": params["quantity"],
              "reason": f"fade favourite mid {best[1]:.3f} at {params['hours_before_close']}h before close"}
    return [intent], info


def intents_for_series(conn, series_ticker, start, end, params):
    """Intents and info dicts for every event of the series with start <= event_date <= end."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker FROM event WHERE series_ticker = %s AND event_date BETWEEN %s AND %s "
            "ORDER BY event_date",
            (series_ticker, start, end),
        )
        event_tickers = [row[0] for row in cur.fetchall()]

    all_intents = []
    all_info = []
    for event_ticker in event_tickers:
        intents, info = intents_for_event(conn, event_ticker, params)
        all_intents.extend(intents)
        all_info.append(info)
    return all_intents, all_info


def main():
    parser = argparse.ArgumentParser(description="Print the midday-fade decisions for a series.")
    parser.add_argument("--series", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()

    with connect(args.db) as conn:
        intents, infos = intents_for_series(conn, args.series, args.start, args.end, dict(DEFAULTS))

    for info in infos:
        if info["skipped"]:
            result = f"skipped: {info['skipped']}"
        else:
            result = f"{info['favourite_market']} {info['favourite_mid']:.3f}"
        print(f"{info['event_ticker']}  {info['decided_at']}  {result}")
    print(f"{len(intents)} intents")


if __name__ == "__main__":
    main()
