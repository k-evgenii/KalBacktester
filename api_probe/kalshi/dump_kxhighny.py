"""Dump the raw KXHIGHNY markets, 1-minute bars and trades for a range of event days.

Run: uv run python api_probe/kalshi/dump_kxhighny.py --start 2026-11-16 --end 2026-11-22
Output: api_probe/kalshi/KXHIGHNY/ (markets.json, bars_1min/, trades/, _report.json)
"""

import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXHIGHNY"
PAUSE = 0.5          # seconds after every request
MAX_BARS = 4999      # API refuses more than 5000 one-minute bars per call

OUT_DIR = Path(__file__).parent / SERIES
NY = ZoneInfo("America/New_York")

request_count = 0


def get(path, params=None):
    """GET BASE+path. Returns (status, parsed_json_or_None).
    Sends header User-Agent: kalbt-probe. Sleeps PAUSE after every request.
    On 429 or 5xx: sleep 5 s and retry, at most 3 retries, then raise RuntimeError.
    On 404: return (404, None). Any other 4xx: raise RuntimeError with the body."""
    global request_count
    url = BASE + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "kalbt-probe"})

    retries = 0
    while True:
        request_count += 1
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                status = response.status
                body = response.read()
        except urllib.error.HTTPError as error:
            status = error.code
            body = error.read()
        time.sleep(PAUSE)

        if status == 200:
            return status, json.loads(body)
        if status == 404:
            return 404, None
        if status == 429 or status >= 500:
            if retries >= 3:
                raise RuntimeError(f"{url}: status {status} after 3 retries")
            retries += 1
            time.sleep(5)
            continue
        raise RuntimeError(f"{url}: status {status}: {body.decode(errors='replace')}")


def event_ticker(day):            # date(2026, 11, 22) -> "KXHIGHNY-26NOV22"
    return f"{SERIES}-{day:%y}{day:%b}".upper() + f"{day:%d}"


def to_epoch(rfc3339):            # "2026-11-21T15:00:00Z" -> int seconds
    return int(datetime.fromisoformat(rfc3339.replace("Z", "+00:00")).timestamp())


def to_ny(rfc3339):
    moment = datetime.fromisoformat(rfc3339.replace("Z", "+00:00"))
    return moment.astimezone(NY).isoformat()


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=1, sort_keys=True))


def fetch_markets(wanted_events):
    """Live markets first, then the historical surface for events the live one lacks."""
    markets = []
    cursor = ""
    while True:
        params = {"series_ticker": SERIES, "limit": 1000, "cursor": cursor}
        _, body = get("/markets", params)
        for market in body["markets"]:
            if market["event_ticker"] in wanted_events:
                markets.append(market)
        cursor = body.get("cursor") or ""
        if not cursor:
            break

    live_events = {market["event_ticker"] for market in markets}
    for event in sorted(wanted_events - live_events):
        _, body = get("/historical/markets", {"event_ticker": event})
        markets.extend(body["markets"])

    found_events = {market["event_ticker"] for market in markets}
    for event in sorted(wanted_events - found_events):
        raise RuntimeError(f"no markets found for event {event}")

    markets.sort(key=lambda market: market["ticker"])
    return markets


def fetch_bars(market):
    """Returns (surface, bars). Tries the live surface, falls back to historical on 404."""
    ticker = market["ticker"]
    start = to_epoch(market["open_time"])
    end = to_epoch(market["close_time"])
    surface = "live"
    bars = []

    lo = start
    first_chunk = True
    while lo <= end:
        hi = min(end, lo + MAX_BARS * 60 - 1)
        params = {"start_ts": lo, "end_ts": hi, "period_interval": 1}
        if surface == "live":
            status, body = get(f"/series/{SERIES}/markets/{ticker}/candlesticks", params)
            if status == 404 and first_chunk:
                surface = "historical"
        if surface == "historical":
            status, body = get(f"/historical/markets/{ticker}/candlesticks", params)
        if body is None:
            raise RuntimeError(f"no candlesticks response for {ticker} ({lo}..{hi})")
        bars.extend(body["candlesticks"])
        first_chunk = False
        lo = hi + 1

    return surface, bars


def fetch_trades(ticker, surface):
    path = "/markets/trades" if surface == "live" else "/historical/trades"
    trades = []
    cursor = ""
    while True:
        _, body = get(path, {"ticker": ticker, "limit": 1000, "cursor": cursor})
        trades.extend(body["trades"])
        cursor = body.get("cursor") or ""
        if not cursor:
            break
    return trades


def bar_close(bar):
    price = bar.get("price") or {}
    return price.get("close_dollars", price.get("close"))


def bar_volume(bar):
    value = bar.get("volume_fp", bar.get("volume"))
    return float(value) if value is not None else 0.0


def is_empty(value):
    return value is None or value == "" or value == {}


def count_nulls(records):
    """For every top-level field seen, how many records have it missing, null, "" or {}."""
    fields = set()
    for record in records:
        fields.update(record.keys())
    return {field: sum(1 for record in records if is_empty(record.get(field))) for field in sorted(fields)}


def count_gaps(bars):
    if not bars:
        return 0
    stamps = [bar["end_period_ts"] for bar in bars]
    expected = (max(stamps) - min(stamps)) // 60 + 1
    return expected - len(set(stamps))


def market_report(market, surface, bars, trades):
    close_ts = to_epoch(market["close_time"])
    open_ts = to_epoch(market["open_time"])
    event_day = datetime.strptime(market["event_ticker"].split("-")[1], "%y%b%d").date()

    flags = []
    if not bars:
        flags.append("no_bars")
    if not trades:
        flags.append("no_trades")
    if is_empty(market.get("result")):
        flags.append("missing_result")
    if is_empty(market.get("expiration_value")):
        flags.append("missing_expiration_value")

    return {
        "surface": surface,
        "status": market.get("status"),
        "result": market.get("result"),
        "expiration_value": market.get("expiration_value"),
        "open_minutes": (close_ts - open_ts) // 60,
        "n_bars": len(bars),
        "n_bars_no_trade": sum(1 for bar in bars if bar_close(bar) is None),
        "n_gaps": count_gaps(bars),
        "n_trades": len(trades),
        "bar_volume": sum(bar_volume(bar) for bar in bars),
        "trade_volume": sum(float(trade["count_fp"]) for trade in trades),
        "n_trades_after_close": sum(1 for trade in trades if to_epoch(trade["created_time"]) > close_ts),
        "event_day": event_day.isoformat(),
        "open_ny": to_ny(market["open_time"]),
        "close_ny": to_ny(market["close_time"]),
        "occurrence_ny": to_ny(market["occurrence_datetime"]) if market.get("occurrence_datetime") else None,
        "flags": flags,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", required=True, type=date.fromisoformat, help="first event day, YYYY-MM-DD")
    parser.add_argument("--end", required=True, type=date.fromisoformat, help="last event day, YYYY-MM-DD")
    args = parser.parse_args()

    days = []
    day = args.start
    while day <= args.end:
        days.append(day)
        day += timedelta(days=1)
    wanted_events = {event_ticker(day) for day in days}

    markets = fetch_markets(wanted_events)
    write_json(OUT_DIR / "markets.json", {"start": args.start.isoformat(), "end": args.end.isoformat(), "markets": markets})
    print(f"{len(markets)} markets in {len(wanted_events)} events")

    market_reports = {}
    all_bars = []
    all_trades = []
    event_has_data = {event: False for event in wanted_events}
    event_counts = {event: 0 for event in wanted_events}

    for market in markets:
        ticker = market["ticker"]
        surface, bars = fetch_bars(market)
        trades = fetch_trades(ticker, surface)
        write_json(OUT_DIR / "bars_1min" / f"{ticker}.json", {"ticker": ticker, "surface": surface, "candlesticks": bars})
        write_json(OUT_DIR / "trades" / f"{ticker}.json", {"ticker": ticker, "surface": surface, "trades": trades})

        market_reports[ticker] = market_report(market, surface, bars, trades)
        all_bars.extend(bars)
        all_trades.extend(trades)
        event_counts[market["event_ticker"]] += 1
        if bars or trades:
            event_has_data[market["event_ticker"]] = True
        print(f"{ticker}: {surface}, {len(bars)} bars, {len(trades)} trades")

    report = {
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "events": {
            event: {"n_markets": event_counts[event], "empty": not event_has_data[event]}
            for event in sorted(wanted_events)
        },
        "markets": market_reports,
        "null_counts": {
            "markets": count_nulls(markets),
            "bars": count_nulls(all_bars),
            "trades": count_nulls(all_trades),
        },
    }
    write_json(OUT_DIR / "_report.json", report)

    flagged = {ticker: r["flags"] for ticker, r in market_reports.items() if r["flags"]}
    print(f"events: {len(wanted_events)}, markets: {len(markets)}, bars: {len(all_bars)}, trades: {len(all_trades)}")
    print(f"markets with flags: {len(flagged)}")
    for ticker, flags in flagged.items():
        print(f"  {ticker}: {', '.join(flags)}")
    print(f"requests made: {request_count}")


if __name__ == "__main__":
    main()
