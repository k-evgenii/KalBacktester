"""Load the one-week KXHIGHNY dump (api_probe/kalshi/KXHIGHNY/) into the reconciliation database.

Run from the repo root: uv run python -m ingest.load_kxhighny_dump
Safe to run twice: every insert is ON CONFLICT DO NOTHING.
"""
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect

DUMP_DIR = Path(__file__).resolve().parent.parent / "api_probe" / "kalshi" / "KXHIGHNY"


def bar_quote(bar, side):  # side is "yes_bid" or "yes_ask"
    quote = bar.get(side) or {}
    value = quote.get("close_dollars", quote.get("close"))
    return Decimal(value) if value is not None else None


def to_decimal(value):
    return Decimal(str(value)) if value is not None else None


def candle_row(market_ticker, bar):
    bid = bar_quote(bar, "yes_bid")
    ask = bar_quote(bar, "yes_ask")
    if bid is not None and bid <= 0:
        bid = None  # a bid of 0.00 means no bid
    if ask is not None and ask >= 1:
        ask = None  # an ask of 1.00 means no ask
    volume = Decimal(bar.get("volume_fp", bar.get("volume")) or "0")
    ts = datetime.fromtimestamp(bar["end_period_ts"], tz=timezone.utc)
    return (market_ticker, ts, bid, ask, volume)


def event_date(event_ticker):
    return datetime.strptime(event_ticker.split("-")[1], "%y%b%d").date()


def main():
    markets = json.loads((DUMP_DIR / "markets.json").read_text())["markets"]
    event_tickers = sorted({m["event_ticker"] for m in markets})
    inserted = {}

    with connect() as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO underlying (underlying_id, description) VALUES (%s, %s) "
            "ON CONFLICT DO NOTHING",
            ("NYC_HIGH", "Daily maximum temperature, New York Central Park (KNYC)"),
        )
        inserted["underlying"] = cur.rowcount

        cur.execute(
            "INSERT INTO series (ticker, underlying_id, title) VALUES (%s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            ("KXHIGHNY", "NYC_HIGH", "Highest temperature in NYC"),
        )
        inserted["series"] = cur.rowcount

        inserted["event"] = 0
        for ticker in event_tickers:
            cur.execute(
                "INSERT INTO event (ticker, series_ticker, underlying_id, event_date, "
                "settlement_source) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (ticker, "KXHIGHNY", "NYC_HIGH", event_date(ticker), "The Weather Company"),
            )
            inserted["event"] += cur.rowcount

        inserted["market"] = 0
        for m in markets:
            cur.execute(
                "INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, "
                "cap_strike, open_time, close_time, result, settled_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (
                    m["ticker"],
                    m["event_ticker"],
                    m["strike_type"],
                    to_decimal(m.get("floor_strike")),
                    to_decimal(m.get("cap_strike")),
                    m["open_time"],
                    m["close_time"],
                    m["result"] or None,
                    m.get("settlement_ts"),
                ),
            )
            inserted["market"] += cur.rowcount

        inserted["candle"] = 0
        for m in markets:
            bars_file = DUMP_DIR / "bars_1min" / f"{m['ticker']}.json"
            bars = json.loads(bars_file.read_text())["candlesticks"]
            rows = [candle_row(m["ticker"], bar) for bar in bars]
            cur.executemany(
                "INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume) "
                "VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                rows,
            )
            inserted["candle"] += cur.rowcount

        conn.commit()

    for table, count in inserted.items():
        print(f"{table}: {count} rows inserted")


if __name__ == "__main__":
    main()
