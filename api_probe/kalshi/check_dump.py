"""Checks on the KXHIGHNY dump for 2026-11-16 .. 2026-11-22 (spec 002, agreed tests 1-5).

Run: uv run python api_probe/kalshi/check_dump.py
Reads markets.json, bars_1min/<ticker>.json and trades/<ticker>.json only.
"""
import json
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DUMP = Path(__file__).parent / "KXHIGHNY"
START = date(2026, 11, 16)
END = date(2026, 11, 22)

results = []  # (label, ok, reason)


def load(path):
    return json.loads(Path(path).read_text())


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def record(label, problems, ok_note=""):
    if problems:
        shown = "; ".join(problems[:5])
        more = f" (+{len(problems) - 5} more)" if len(problems) > 5 else ""
        print(f"[FAIL] {label}: {shown}{more}")
        results.append(False)
    else:
        print(f"[PASS] {label}" + (f": {ok_note}" if ok_note else ""))
        results.append(True)


def expected_events():
    events = []
    day = START
    while day <= END:
        events.append(f"KXHIGHNY-{day:%y}{day:%b}".upper() + f"{day:%d}")
        day += timedelta(days=1)
    return events


def check_1_events_and_status(markets):
    problems = []
    found = {m["event_ticker"] for m in markets}
    want = set(expected_events())
    if found != want:
        problems.append(f"events differ: missing {sorted(want - found)}, extra {sorted(found - want)}")
    if len(found) != 7:
        problems.append(f"{len(found)} distinct events, expected 7")
    tickers = [m["ticker"] for m in markets]
    if len(set(tickers)) != len(tickers):
        problems.append("duplicate tickers in markets.json")
    for m in markets:
        if m.get("status") != "finalized":
            problems.append(f"{m['ticker']} status={m.get('status')!r}")
    record("1 seven expected events, all markets finalized", problems, f"{len(markets)} markets")


def check_2_files_and_bar_count(markets):
    problems = []
    for m in markets:
        t = m["ticker"]
        bars_path = DUMP / "bars_1min" / f"{t}.json"
        trades_path = DUMP / "trades" / f"{t}.json"
        if not bars_path.exists():
            problems.append(f"{t} no bars file")
        else:
            n_bars = len(load(bars_path)["candlesticks"])
            seconds = (parse_time(m["close_time"]) - parse_time(m["open_time"])).total_seconds()
            open_minutes = int(seconds // 60)
            if n_bars > open_minutes + 1:
                problems.append(f"{t} n_bars={n_bars} > open_minutes+1={open_minutes + 1}")
        if not trades_path.exists():
            problems.append(f"{t} no trades file")
    record("2 bars and trades file per market, n_bars <= open_minutes+1", problems)


def check_3_settlement(markets):
    problems = []
    n_checked = 0
    for m in markets:
        t = m["ticker"]
        raw = m.get("expiration_value")
        if raw in (None, ""):
            print(f"  note: {t} has empty expiration_value (result={m.get('result')!r}), skipped")
            continue
        v = float(raw)
        kind = m.get("strike_type")
        if kind == "greater":
            expect_yes = v > m["floor_strike"]
        elif kind == "less":
            expect_yes = v < m["cap_strike"]
        elif kind == "between":
            expect_yes = m["floor_strike"] <= v <= m["cap_strike"]
        else:
            problems.append(f"{t} unknown strike_type {kind!r}")
            continue
        n_checked += 1
        is_yes = m.get("result") == "yes"
        if is_yes != expect_yes:
            problems.append(f"{t} {kind} v={v} floor={m.get('floor_strike')} cap={m.get('cap_strike')} result={m.get('result')!r}")
    record("3 settlement consistent with expiration_value", problems, f"{n_checked} markets checked")


def check_4_bars_sorted(markets):
    problems = []
    for m in markets:
        t = m["ticker"]
        path = DUMP / "bars_1min" / f"{t}.json"
        if not path.exists():
            problems.append(f"{t} no bars file")
            continue
        stamps = [b["end_period_ts"] for b in load(path)["candlesticks"]]
        if len(set(stamps)) != len(stamps):
            problems.append(f"{t} duplicate end_period_ts")
        if stamps != sorted(stamps):
            problems.append(f"{t} not ascending")
    record("4 bars sorted ascending, no duplicate end_period_ts", problems)


def check_5_trades(markets):
    problems = []
    for m in markets:
        t = m["ticker"]
        path = DUMP / "trades" / f"{t}.json"
        if not path.exists():
            problems.append(f"{t} no trades file")
            continue
        trades = load(path)["trades"]
        lo = parse_time(m["open_time"])
        hi = parse_time(m["latest_expiration_time"])
        ids = [x["trade_id"] for x in trades]
        if len(set(ids)) != len(ids):
            problems.append(f"{t} repeated trade_id")
        times = []
        for x in trades:
            try:
                times.append(parse_time(x["created_time"]))
            except (KeyError, ValueError, TypeError):
                problems.append(f"{t} trade {x.get('trade_id')} bad created_time {x.get('created_time')!r}")
        if times != sorted(times, reverse=True):
            problems.append(f"{t} not sorted newest first")
        outside = [c for c in times if c < lo or c > hi]
        if outside:
            problems.append(f"{t} {len(outside)} trades outside [open_time, latest_expiration_time]")
    record("5 trades newest first, unique trade_id, times within window", problems)


def extra_ticker_matches_files(markets):
    """EXTRA: files in bars_1min/ and trades/ correspond one to one with markets, and carry the market's ticker."""
    problems = []
    tickers = {m["ticker"] for m in markets}
    for folder, key in (("bars_1min", "candlesticks"), ("trades", "trades")):
        names = {p.stem for p in (DUMP / folder).glob("*.json")}
        if names != tickers:
            problems.append(f"{folder} files differ from markets: {sorted(names ^ tickers)[:3]}")
        for t in tickers & names:
            body = load(DUMP / folder / f"{t}.json")
            if body.get("ticker") != t:
                problems.append(f"{folder}/{t} inner ticker {body.get('ticker')!r}")
            if body.get("surface") not in ("live", "historical"):
                problems.append(f"{folder}/{t} surface {body.get('surface')!r}")
            if key not in body:
                problems.append(f"{folder}/{t} missing {key}")
    record("EXTRA files match markets, ticker and surface fields valid", problems)


def extra_bar_surface_equals_trade_surface(markets):
    """EXTRA: a market's bars and trades use the same surface (spec: trades on the same surface as bars)."""
    problems = []
    for m in markets:
        t = m["ticker"]
        a = load(DUMP / "bars_1min" / f"{t}.json")["surface"]
        b = load(DUMP / "trades" / f"{t}.json")["surface"]
        if a != b:
            problems.append(f"{t} bars={a} trades={b}")
    record("EXTRA bars and trades surface agree", problems)


def extra_markets_sorted(markets):
    """EXTRA: markets.json is sorted by ticker."""
    tickers = [m["ticker"] for m in markets]
    problems = [] if tickers == sorted(tickers) else ["markets not sorted by ticker"]
    record("EXTRA markets sorted by ticker", problems)


def extra_trades_have_volume_and_market_has_trades(markets):
    """EXTRA: the dump is not hollow, i.e. most events contain bars and trades."""
    problems = []
    per_event = {}
    for m in markets:
        t = m["ticker"]
        n_b = len(load(DUMP / "bars_1min" / f"{t}.json")["candlesticks"])
        n_t = len(load(DUMP / "trades" / f"{t}.json")["trades"])
        per_event.setdefault(m["event_ticker"], 0)
        per_event[m["event_ticker"]] += n_b + n_t
    for e, n in sorted(per_event.items()):
        if n == 0:
            problems.append(f"{e} has no bars and no trades")
    record("EXTRA every event has some bars or trades", problems)


def main():
    markets = load(DUMP / "markets.json")["markets"]
    check_1_events_and_status(markets)
    check_2_files_and_bar_count(markets)
    check_3_settlement(markets)
    check_4_bars_sorted(markets)
    check_5_trades(markets)
    extra_ticker_matches_files(markets)
    extra_bar_surface_equals_trade_surface(markets)
    extra_markets_sorted(markets)
    extra_trades_have_volume_and_market_has_trades(markets)
    n_fail = results.count(False)
    print(f"{len(results) - n_fail} passed, {n_fail} failed")
    sys.exit(1 if n_fail else 0)


if __name__ == "__main__":
    main()
