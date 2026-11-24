"""QA gate for a raw daily-high series dump folder (spec 004; any series since A020).

Run from the repo root: uv run python -m ingest.qa_kxhighny_dump [dump_dir]
Reads the dump files and the database (SELECT only), prints one line per check, writes
_qa_report.json into the dump folder, exits 0 on PASS and 1 on BLOCK.
"""
import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

from db.connect import connect
from ingest.load_kxhighny_dump import DUMP_DIR, TWC_FROM, candle_row, event_date, series_of, settlement_source

MISSING = object()  # a bar key that is absent under both key styles
MARKET_FIELDS = ["ticker", "event_ticker", "strike_type", "open_time", "close_time", "status"]


# ---------- small helpers ----------

def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def to_decimal(value):
    """Decimal from a JSON value; raises InvalidOperation for text that is not a finite number."""
    number = Decimal(str(value))
    if not number.is_finite():
        raise InvalidOperation(f"not finite: {value}")
    return number


def minute_label(ts):
    return ts.strftime("%Y-%m-%dT%H:%MZ")


def raw_quote(bar, side):
    quote = bar.get(side)
    if not isinstance(quote, dict):
        return MISSING
    if "close_dollars" in quote:
        return quote["close_dollars"]
    if "close" in quote:
        return quote["close"]
    return MISSING


def raw_volume(bar):
    if "volume_fp" in bar:
        return bar["volume_fp"]
    if "volume" in bar:
        return bar["volume"]
    return MISSING


def bar_label(ticker, bar, index):
    ts = bar.get("end_period_ts")
    if isinstance(ts, int) and not isinstance(ts, bool):
        return f"{ticker} @ {minute_label(datetime.fromtimestamp(ts, tz=timezone.utc))}"
    return f"{ticker} bar #{index}"


def floor_of(m):
    return Decimal(str(m["floor_strike"]))


def cap_of(m):
    return Decimal(str(m["cap_strike"]))


def make_check(check_id, name, severity, problems, ok_detail="ok"):
    if problems:
        return {"id": check_id, "name": name, "severity": severity, "status": "fail",
                "detail": f"{len(problems)} problem(s)", "examples": problems[:10]}
    return {"id": check_id, "name": name, "severity": severity, "status": "pass",
            "detail": ok_detail, "examples": []}


def make_mixed_check(check_id, name, block_problems, warn_problems, ok_detail="ok"):
    """A check with BLOCK problems and WARN problems: BLOCK wins when both exist."""
    if not block_problems and not warn_problems:
        return make_check(check_id, name, "BLOCK", [], ok_detail)
    severity = "BLOCK" if block_problems else "WARN"
    detail = f"{len(block_problems)} blocking, {len(warn_problems)} warning"
    return {"id": check_id, "name": name, "severity": severity, "status": "fail",
            "detail": detail, "examples": (block_problems + warn_problems)[:10]}


def skipped_check(check_id, name, severity):
    return {"id": check_id, "name": name, "severity": severity, "status": "skipped",
            "detail": "skipped: an earlier BLOCK check failed", "examples": []}


def read_json(path, problems):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError) as error:
        problems.append(f"{path.name}: {error}")
        return None


def events_of(markets):
    events = {}
    for m in markets:
        events.setdefault(m["event_ticker"], []).append(m)
    return events


# ---------- checks 1-6: can the dump be read at all ----------

def check_1(ctx):
    problems = []
    dump_dir = ctx["dump_dir"]
    doc = read_json(dump_dir / "markets.json", problems)
    if doc is not None and not (isinstance(doc.get("markets"), list) and "start" in doc and "end" in doc):
        problems.append("markets.json: needs start, end and a markets list")
        doc = None
    ctx["markets_doc"] = doc
    ctx["markets"] = doc["markets"] if doc else []

    ctx["series"] = None
    if doc:
        try:
            ctx["series"] = series_of(ctx["markets"])
        except (KeyError, TypeError, AttributeError, ValueError) as error:
            problems.append(f"series: {error}")

    report_doc = read_json(dump_dir / "_report.json", []) if (dump_dir / "_report.json").exists() else {}
    ctx["no_trades"] = isinstance(report_doc, dict) and report_doc.get("trades") is False

    for folder, list_key, store in [("bars_1min", "candlesticks", "bars_files"), ("trades", "trades", "trades_files")]:
        ctx[store] = {}
        path = dump_dir / folder
        if folder == "trades" and ctx["no_trades"] and not path.exists():
            continue
        if not path.is_dir():
            problems.append(f"{folder}/ is not a folder")
            continue
        for file in sorted(path.iterdir()):
            content = read_json(file, problems)
            if content is None:
                continue
            if not isinstance(content, dict) or not isinstance(content.get(list_key), list):
                problems.append(f"{folder}/{file.name}: no {list_key} list")
                continue
            ctx[store][file.stem] = content
    return make_check(1, "files exist and parse", "BLOCK", problems)


def check_2(ctx):
    problems = []
    doc = ctx["markets_doc"]
    try:
        start = date.fromisoformat(doc["start"])
        end = date.fromisoformat(doc["end"])
    except ValueError as error:
        return make_check(2, "date range and events", "BLOCK", [f"start/end: {error}"])
    if start > end:
        problems.append(f"start {start} is after end {end}")
    expected = set()
    day = start
    while day <= end:
        expected.add(ctx["series"] + "-" + day.strftime("%y%b%d").upper())
        day += timedelta(days=1)
    found = {m.get("event_ticker") for m in ctx["markets"]}
    if found != expected:
        problems.append(f"events differ: missing {sorted(expected - found)}, extra {sorted(found - expected, key=str)}")
    return make_check(2, "date range and events", "BLOCK", problems)


def check_3(ctx):
    block, warn = [], []
    tickers = {m.get("ticker") for m in ctx["markets"]}
    for folder, store, problems in [("bars_1min", "bars_files", block), ("trades", "trades_files", warn)]:
        files = ctx[store]
        if folder == "trades" and ctx["no_trades"]:
            warn.append("dump has no trades (fetched with --no-trades)")
            continue
        for ticker in sorted(t for t in tickers if t not in files):
            problems.append(f"{folder}/{ticker}.json is missing")
        for stem in sorted(files):
            if stem not in tickers:
                block.append(f"{folder}/{stem}.json has no market")
            if files[stem].get("ticker") != stem:
                block.append(f"{folder}/{stem}.json: ticker is {files[stem].get('ticker')!r}")
            if files[stem].get("surface") not in ("live", "historical"):
                block.append(f"{folder}/{stem}.json: surface is {files[stem].get('surface')!r}")
    for stem, bars_file in ctx["bars_files"].items():
        trades_file = ctx["trades_files"].get(stem)
        if trades_file is not None and trades_file.get("surface") != bars_file.get("surface"):
            block.append(f"{stem}: surface differs between bars and trades files")
    return make_mixed_check(3, "bars and trades files match the markets", block, warn)


def check_4(ctx):
    problems = []
    for m in ctx["markets"]:
        name = m.get("ticker", "?")
        missing = [f for f in MARKET_FIELDS if f not in m]
        if missing:
            problems.append(f"{name}: missing {missing}")
            continue
        for field in ("open_time", "close_time"):
            try:
                parse_time(m[field])
            except (ValueError, AttributeError):
                problems.append(f"{name}: {field} {m[field]!r} does not parse")
        needed = {"less": ["cap_strike"], "greater": ["floor_strike"], "between": ["floor_strike", "cap_strike"]}
        if m["strike_type"] not in needed:
            problems.append(f"{name}: unknown strike_type {m['strike_type']!r}")
            continue
        for field in needed[m["strike_type"]]:
            try:
                to_decimal(m[field])
            except (KeyError, InvalidOperation):
                problems.append(f"{name}: {field} missing or not a number")
    return make_check(4, "market fields parse", "BLOCK", problems)


def check_5(ctx):
    problems = []
    for ticker, bars_file in ctx["bars_files"].items():
        for i, bar in enumerate(bars_file["candlesticks"]):
            label = bar_label(ticker, bar, i)
            ts = bar.get("end_period_ts")
            if not isinstance(ts, int) or isinstance(ts, bool):
                problems.append(f"{label}: end_period_ts is not an int")
            for name, raw in [("yes_bid", raw_quote(bar, "yes_bid")), ("yes_ask", raw_quote(bar, "yes_ask")),
                              ("volume", raw_volume(bar))]:
                if raw is MISSING:
                    continue  # check 6 reports it
                try:
                    number = to_decimal(raw)
                except InvalidOperation:
                    problems.append(f"{label}: {name} {raw!r} is not a number")
                    continue
                if name != "volume" and not 0 <= number <= 1:
                    problems.append(f"{label}: {name} {number} outside [0, 1]")
                if name == "volume" and number < 0:
                    problems.append(f"{label}: volume {number} is negative")
    return make_check(5, "bars parse and are in range", "BLOCK", problems)


def check_6(ctx):
    problems = []
    for ticker, bars_file in ctx["bars_files"].items():
        for i, bar in enumerate(bars_file["candlesticks"]):
            absent = [name for name, raw in [("yes_bid", raw_quote(bar, "yes_bid")),
                                             ("yes_ask", raw_quote(bar, "yes_ask")),
                                             ("volume", raw_volume(bar))] if raw is MISSING]
            if absent:
                problems.append(f"{bar_label(ticker, bar, i)}: missing {absent}")
    return make_check(6, "bars have bid, ask and volume", "BLOCK", problems)


# ---------- parsed bars, used by checks 11, 15, 18, 19, 21 ----------

def parse_bars(ctx):
    """ctx["bars"][ticker] = [(ts, bid, ask, volume)] with the 0 -> None and 1 -> None translation."""
    ctx["bars"] = {}
    ctx["bid_0_to_null"] = 0
    ctx["ask_1_to_null"] = 0
    for ticker, bars_file in ctx["bars_files"].items():
        rows = []
        for bar in bars_file["candlesticks"]:
            raw_bid = to_decimal(raw_quote(bar, "yes_bid"))
            raw_ask = to_decimal(raw_quote(bar, "yes_ask"))
            if raw_bid <= 0:
                ctx["bid_0_to_null"] += 1
            if raw_ask >= 1:
                ctx["ask_1_to_null"] += 1
            _, ts, bid, ask, volume = candle_row(ticker, bar)
            rows.append((ts, bid, ask, volume))
        ctx["bars"][ticker] = rows


# ---------- checks 7-14: domain rules ----------

def check_7(ctx):
    problems = []
    for m in ctx["markets"]:
        kind = m["strike_type"]
        has_floor = "floor_strike" in m
        has_cap = "cap_strike" in m
        if kind == "less" and not (has_cap and not has_floor):
            problems.append(f"{m['ticker']}: less needs cap_strike only")
        elif kind == "greater" and not (has_floor and not has_cap):
            problems.append(f"{m['ticker']}: greater needs floor_strike only")
        elif kind == "between" and not (has_floor and has_cap and floor_of(m) < cap_of(m)):
            problems.append(f"{m['ticker']}: between needs floor_strike < cap_strike")
    return make_check(7, "strike shape", "BLOCK", problems)


def check_9(ctx):
    problems = []
    for m in ctx["markets"]:
        if m["status"] != "finalized":
            problems.append(f"{m['ticker']}: status is {m['status']!r}")
        elif m.get("result") not in ("yes", "no"):
            problems.append(f"{m['ticker']}: result is {m.get('result')!r}")
        else:
            try:
                parse_time(m["settlement_ts"])
            except (KeyError, ValueError, AttributeError):
                problems.append(f"{m['ticker']}: settlement_ts missing or does not parse")
    return make_check(9, "every market is settled", "BLOCK", problems)


def check_10(ctx):
    problems = []
    for m in ctx["markets"]:
        if not m.get("expiration_value"):
            continue
        try:
            value = Decimal(m["expiration_value"])
        except InvalidOperation:
            problems.append(f"{m['ticker']}: expiration_value {m['expiration_value']!r} is not a number")
            continue
        floor = Decimal(str(m["floor_strike"])) if "floor_strike" in m else None
        cap = Decimal(str(m["cap_strike"])) if "cap_strike" in m else None
        if m["strike_type"] == "greater":
            expected = "yes" if value > floor else "no"
        elif m["strike_type"] == "less":
            expected = "yes" if value < cap else "no"
        else:
            expected = "yes" if floor <= value <= cap else "no"
        if m.get("result") != expected:
            problems.append(f"{m['ticker']}: value {value} gives {expected}, result is {m.get('result')!r}")
    return make_check(10, "settlement rule", "BLOCK", problems)


def check_11(ctx):
    block, warn = [], []
    for m in ctx["markets"]:
        open_time = parse_time(m["open_time"])
        close_time = parse_time(m["close_time"])
        previous = None
        for ts, bid, ask, _ in ctx["bars"][m["ticker"]]:
            label = f"{m['ticker']} @ {minute_label(ts)}"
            if previous is not None and ts <= previous:
                block.append(f"{label}: not after the previous bar")
            if not open_time < ts <= close_time:
                block.append(f"{label}: outside open_time..close_time")
            if bid is not None and ask is not None and bid >= ask:
                warn.append(f"{label}: bid {bid} >= ask {ask}")
            previous = ts
    return make_mixed_check(11, "bar times and crossed quotes", block, warn)


def check_12(ctx):
    problems = []
    for event, ms in events_of(ctx["markets"]).items():
        less = [m for m in ms if m["strike_type"] == "less"]
        greater = [m for m in ms if m["strike_type"] == "greater"]
        between = sorted((m for m in ms if m["strike_type"] == "between"), key=floor_of)
        if not (len(less) == 1 and len(greater) == 1 and len(between) >= 1):
            problems.append(f"{event}: needs one less, one greater and at least one between")
            continue
        if cap_of(less[0]) != floor_of(between[0]):
            problems.append(f"{event}: less cap {cap_of(less[0])} != first floor {floor_of(between[0])}")
        for a, b in zip(between, between[1:]):
            if cap_of(a) + 1 != floor_of(b):
                problems.append(f"{event}: gap or overlap between {a['ticker']} and {b['ticker']}")
        if floor_of(greater[0]) != cap_of(between[-1]):
            problems.append(f"{event}: greater floor {floor_of(greater[0])} != last cap {cap_of(between[-1])}")
    return make_check(12, "strikes tile", "BLOCK", problems)


def check_13(ctx):
    problems = []
    for event, ms in events_of(ctx["markets"]).items():
        yes_count = sum(1 for m in ms if m.get("result") == "yes")
        if yes_count != 1:
            problems.append(f"{event}: {yes_count} markets resolved yes")
    return make_check(13, "one winner per event", "BLOCK", problems)


def check_14(ctx, conn):
    station = station_of(conn, ctx["series"])
    if station is None:
        return {"id": 14, "name": "event size and times", "severity": "WARN", "status": "fail",
                "detail": f"station for {ctx['series']} unknown: cannot check times", "examples": []}
    tz = ZoneInfo(station[3])
    problems = []
    for event, ms in events_of(ctx["markets"]).items():
        if len(ms) != 6:
            problems.append(f"{event}: {len(ms)} markets, expected 6")
        day = event_date(event)
        day_before = day - timedelta(days=1)
        day_after = day + timedelta(days=1)
        expected_open = datetime(day_before.year, day_before.month, day_before.day, 10, 0, tzinfo=ZoneInfo("America/New_York"))
        allowed_closes = {
            datetime(day_after.year, day_after.month, day_after.day, 0, 59, tzinfo=tz),
            datetime(day_after.year, day_after.month, day_after.day, 1, 0, tzinfo=tz),
        }
        for m in ms:
            if parse_time(m["open_time"]) != expected_open:
                problems.append(f"{m['ticker']}: open_time {m['open_time']} is not 10:00 ET the day before")
            if parse_time(m["close_time"]) not in allowed_closes:
                problems.append(f"{m['ticker']}: close_time {m['close_time']} is not 00:59/01:00 local the day after")
    return make_check(14, "event size and times", "WARN", problems)


def check_15(ctx):
    problems = []
    for event, ms in events_of(ctx["markets"]).items():
        quotes = []  # one {ts: (bid, ask)} per market, only bars with both sides present
        for m in ms:
            quotes.append({ts: (bid, ask) for ts, bid, ask, _ in ctx["bars"][m["ticker"]]
                           if bid is not None and ask is not None})
        common = set.intersection(*(set(q) for q in quotes))
        for ts in sorted(common):
            bid_sum = sum(q[ts][0] for q in quotes)
            ask_sum = sum(q[ts][1] for q in quotes)
            if ask_sum < 1 or bid_sum > 1:
                problems.append(f"{event} @ {minute_label(ts)}: bid sum {bid_sum}, ask sum {ask_sum}")
    return make_check(15, "quote sums across an event", "WARN", problems)


# ---------- check 16: same key, different value, against the database ----------

def check_16(ctx, conn):
    problems = []
    dump_tickers = [m["ticker"] for m in ctx["markets"]]
    columns = ["ticker", "event_ticker", "strike_type", "floor_strike", "cap_strike",
               "open_time", "close_time", "result", "settled_at"]
    incoming = {}
    for m in ctx["markets"]:
        incoming[m["ticker"]] = (
            m["ticker"], m["event_ticker"], m["strike_type"],
            Decimal(str(m["floor_strike"])) if "floor_strike" in m else None,
            Decimal(str(m["cap_strike"])) if "cap_strike" in m else None,
            parse_time(m["open_time"]), parse_time(m["close_time"]),
            m.get("result") or None,
            parse_time(m["settlement_ts"]) if m.get("settlement_ts") else None,
        )

    with conn.cursor() as cur:
        cur.execute("""SELECT ticker, event_ticker, strike_type, floor_strike, cap_strike,
                              open_time, close_time, result, settled_at
                       FROM market WHERE ticker = ANY(%s)""", (dump_tickers,))
        for row in cur.fetchall():
            row = tuple(row)
            if row != incoming[row[0]]:
                first = next(i for i in range(len(columns)) if row[i] != incoming[row[0]][i])
                problems.append(f"{row[0]}: {columns[first]} stored {row[first]} vs dump {incoming[row[0]][first]}")

        cur.execute("""SELECT market_ticker, ts, yes_bid, yes_ask, volume
                       FROM candle WHERE market_ticker = ANY(%s)""", (dump_tickers,))
        stored = {(row[0], row[1]): tuple(row[2:]) for row in cur.fetchall()}

    for ticker, bars_file in ctx["bars_files"].items():
        for bar in bars_file["candlesticks"]:
            _, ts, bid, ask, volume = candle_row(ticker, bar)
            old = stored.get((ticker, ts))
            if old is not None and old != (bid, ask, volume):
                problems.append(f"{ticker} @ {ts.isoformat()}: stored {old} vs dump {(bid, ask, volume)}")
    return make_check(16, "same key, different value (database)", "BLOCK", problems)


# ---------- checks 18-21: coverage and quality ----------

def check_18(ctx):
    problems = []
    for m in ctx["markets"]:
        ticker = m["ticker"]
        n_bars = len(ctx["bars"][ticker])
        trades_file = ctx["trades_files"].get(ticker)
        open_minutes = (parse_time(m["close_time"]) - parse_time(m["open_time"])).total_seconds() / 60
        if n_bars == 0:
            problems.append(f"{ticker}: no bars")
        elif n_bars / open_minutes < 0.05:
            problems.append(f"{ticker}: bars on {n_bars / open_minutes:.1%} of open minutes")
        if not ctx["no_trades"] and (trades_file is None or not trades_file["trades"]):
            problems.append(f"{ticker}: no trades")
    return make_check(18, "coverage", "WARN", problems)


def check_19(ctx):
    if ctx["no_trades"]:
        return make_check(19, "bar volume vs trade size", "WARN", [], "skipped: no trades")
    problems = []
    for m in ctx["markets"]:
        ticker = m["ticker"]
        trades_file = ctx["trades_files"].get(ticker)
        if trades_file is None:
            continue  # check 3 already warned
        bar_volume = sum(volume for _, _, _, volume in ctx["bars"][ticker])
        trade_volume = sum(Decimal(str(t["count_fp"])) for t in trades_file["trades"])
        if abs(bar_volume - trade_volume) > Decimal("0.5"):
            problems.append(f"{ticker}: bar volume {bar_volume} vs trade size {trade_volume}")
    return make_check(19, "bar volume vs trade size", "WARN", problems)


def check_20(ctx):
    if ctx["no_trades"]:
        return make_check(20, "late trades and empty expiration_value", "WARN", [], "skipped: no trades")
    problems = []
    for m in ctx["markets"]:
        ticker = m["ticker"]
        close_time = parse_time(m["close_time"])
        trades_file = ctx["trades_files"].get(ticker, {"trades": []})
        for t in trades_file["trades"]:
            if parse_time(t["created_time"]) > close_time:
                problems.append(f"{ticker}: trade {t.get('trade_id')} at {t['created_time']} after close")
        if not m.get("expiration_value"):
            problems.append(f"{ticker}: settled with empty or missing expiration_value")
    return make_check(20, "late trades and empty expiration_value", "WARN", problems)


def check_21(ctx):
    detail = f"bid_0_to_null={ctx['bid_0_to_null']}, ask_1_to_null={ctx['ask_1_to_null']}"
    return make_check(21, "translation counts", "WARN", [], detail)


# ---------- checks 22-23: the rules text names the right station and source ----------

def station_of(conn, series):
    """(station_id, cli_pil, rules_names, tz_name) of the series' station, or None."""
    with conn.cursor() as cur:
        cur.execute("""SELECT s.station_id, s.cli_pil, s.rules_names, s.tz_name
                       FROM series se
                       JOIN underlying u ON u.underlying_id = se.underlying_id
                       JOIN station s    ON s.station_id = u.station_id
                       WHERE se.ticker = %s""", (series,))
        return cur.fetchone()


def check_22(ctx, conn):
    row = station_of(conn, ctx["series"])
    if row is None:
        return {"id": 22, "name": "rules name the station", "severity": "BLOCK", "status": "fail",
                "detail": f"station for {ctx['series']} unknown: seed the station and link the underlying before loading",
                "examples": []}
    station_id, cli_pil, names, _ = row
    if not names:
        return {"id": 22, "name": "rules name the station", "severity": "BLOCK", "status": "fail",
                "detail": f"station {station_id} has no rules_names: fill them from the survey",
                "examples": []}
    problems = []
    for m in ctx["markets"]:
        rules = m.get("rules_primary", "")
        codes = re.findall(r"\(CLI[A-Z]{3}\)", rules)
        if codes:
            station_named = all(code == "(" + cli_pil + ")" for code in codes)
        else:
            station_named = any(name in rules for name in names)
        if not station_named:
            problems.append(f"{m.get('ticker', '?')}: {rules[:80]}")
    return make_check(22, "rules name the station", "BLOCK", problems)


def check_23(ctx):
    problems = []
    for m in ctx["markets"]:
        ticker = m.get("ticker", "?")
        source = settlement_source(m.get("rules_primary", ""))
        day = event_date(m["event_ticker"])
        expected = "The Weather Company" if day >= TWC_FROM else "National Weather Service"
        if source is None:
            problems.append(f"{ticker}: no single known source in rules")
        elif source != expected:
            problems.append(f"{ticker}: names {source}, event day {day} expects {expected}")
    return make_check(23, "rules name the settlement source for the date", "BLOCK", problems)


# ---------- the gate ----------

def run_checks(dump_dir, conn):
    ctx = {"dump_dir": Path(dump_dir)}
    # (id, name, severity, function); name and severity are used when a check is skipped
    steps = [
        (1, "files exist and parse", "BLOCK", check_1),
        (2, "date range and events", "BLOCK", check_2),
        (3, "bars and trades files match the markets", "BLOCK", check_3),
        (4, "market fields parse", "BLOCK", check_4),
        (5, "bars parse and are in range", "BLOCK", check_5),
        (6, "bars have bid, ask and volume", "BLOCK", check_6),
        (7, "strike shape", "BLOCK", check_7),
        (9, "every market is settled", "BLOCK", check_9),
        (10, "settlement rule", "BLOCK", check_10),
        (11, "bar times and crossed quotes", "BLOCK", check_11),
        (12, "strikes tile", "BLOCK", check_12),
        (13, "one winner per event", "BLOCK", check_13),
        (14, "event size and times", "WARN", lambda c: check_14(c, conn)),
        (15, "quote sums across an event", "WARN", check_15),
        (16, "same key, different value (database)", "BLOCK", lambda c: check_16(c, conn)),
        (18, "coverage", "WARN", check_18),
        (19, "bar volume vs trade size", "WARN", check_19),
        (20, "late trades and empty expiration_value", "WARN", check_20),
        (21, "translation counts", "WARN", check_21),
        (22, "rules name the station", "BLOCK", lambda c: check_22(c, conn)),
        (23, "rules name the settlement source for the date", "BLOCK", check_23),
    ]
    ctx["bid_0_to_null"] = 0
    ctx["ask_1_to_null"] = 0
    ctx["bars"] = {}

    checks = []
    blocked = False  # any BLOCK check failed: the verdict is BLOCK
    unreadable = False  # a check among 1-6 failed: later checks are skipped
    for check_id, name, severity, function in steps:
        if unreadable:
            checks.append(skipped_check(check_id, name, severity))
            continue
        result = function(ctx)
        checks.append(result)
        if result["status"] == "fail" and result["severity"] == "BLOCK":
            blocked = True
            if check_id <= 6:
                unreadable = True
        if check_id == 6 and not unreadable:
            parse_bars(ctx)  # checks 7 onwards can rely on every bar being readable

    n_trades = sum(len(f["trades"]) for f in ctx.get("trades_files", {}).values())
    return {
        "verdict": "BLOCK" if blocked else "PASS",
        "dump_dir": str(dump_dir),
        "checked_at": datetime.now(timezone.utc).isoformat(),
        "checks": checks,
        "counts": {
            "markets": len(ctx.get("markets", [])),
            "events": len({m.get("event_ticker") for m in ctx.get("markets", [])}),
            "bars": sum(len(rows) for rows in ctx["bars"].values()),
            "trades": n_trades,
            "bid_0_to_null": ctx["bid_0_to_null"],
            "ask_1_to_null": ctx["ask_1_to_null"],
        },
    }


def main():
    dump_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else DUMP_DIR
    with connect() as conn:
        report = run_checks(dump_dir, conn)
    for check in report["checks"]:
        if check["status"] == "pass":
            tag = "PASS"
        elif check["status"] == "skipped":
            tag = "SKIP"
        else:
            tag = "FAIL" if check["severity"] == "BLOCK" else "WARN"
        print(f"[{tag}] {check['id']} {check['name']}: {check['detail']}")
        for example in check["examples"]:
            print(f"        {example}")
    print(f"Verdict: {report['verdict']}")
    if dump_dir.is_dir():
        (dump_dir / "_qa_report.json").write_text(json.dumps(report, indent=2, default=str))
    else:
        print(f"dump folder not found: {dump_dir}, report not written")
    sys.exit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
