"""Load a daily-high series dump (default api_probe/kalshi/KXHIGHNY/) into the reconciliation database.

Run from the repo root: uv run python -m ingest.load_kxhighny_dump [dump_dir]
The QA gate runs first; on BLOCK nothing is inserted.
Safe to run twice: every insert is ON CONFLICT DO NOTHING.
"""
import json
import sys
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect

DUMP_DIR = Path(__file__).resolve().parent.parent / "api_probe" / "kalshi" / "KXHIGHNY"
TWC_FROM = date(2026, 8, 14)  # first event day Kalshi settles daily highs on The Weather Company


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


def series_of(markets):
    """The one series ticker of a dump's markets (event ticker part before the first '-')."""
    found = {m["event_ticker"].split("-")[0] for m in markets}
    if len(found) != 1:
        raise ValueError(f"a dump must hold exactly one series, found {sorted(found)}")
    return found.pop()


def event_date(event_ticker):
    return datetime.strptime(event_ticker.split("-")[1], "%y%b%d").date()


def settlement_source(rules):
    """The source a rules text names: 'The Weather Company' or 'National Weather Service'.
    None when it names neither or both."""
    twc = "The Weather Company" in rules
    nws = "National Weather Service" in rules
    if twc == nws:
        return None
    return "The Weather Company" if twc else "National Weather Service"


def main(dump_dir=DUMP_DIR):
    markets = json.loads((dump_dir / "markets.json").read_text())["markets"]
    event_tickers = sorted({m["event_ticker"] for m in markets})
    inserted = {}

    with connect() as conn, conn.cursor() as cur:
        series = series_of(markets)
        cur.execute("SELECT underlying_id FROM series WHERE ticker = %s", (series,))
        found = cur.fetchone()
        if found is None:
            sys.exit(f"series {series} is not seeded: run python -m db.migrate")
        underlying_id = found[0]

        from ingest.qa_kxhighny_dump import run_checks  # import here, not at the top: the gate imports this module

        report = run_checks(dump_dir, conn)
        (dump_dir / "_qa_report.json").write_text(json.dumps(report, indent=2, default=str))
        if report["verdict"] != "PASS":
            print("QA gate: BLOCK, nothing inserted. See _qa_report.json in the dump folder.")
            sys.exit(1)

        inserted["event"] = 0
        for ticker in event_tickers:
            first_market = min((m for m in markets if m["event_ticker"] == ticker), key=lambda m: m["ticker"])
            source = settlement_source(first_market.get("rules_primary", ""))
            if source is None:
                sys.exit(f"{first_market['ticker']}: rules name no single known settlement source")
            cur.execute(
                "INSERT INTO event (ticker, series_ticker, underlying_id, event_date, "
                "settlement_source) VALUES (%s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                (ticker, series, underlying_id, event_date(ticker), source),
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
            bars_file = dump_dir / "bars_1min" / f"{m['ticker']}.json"
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
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else DUMP_DIR)
