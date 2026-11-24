"""Checks the midday_fade strategy and the harness --strategy option (spec B021, agreed tests 1 to 11).

Tests 1 to 8 run on a throwaway database kalbt_b021_test_<pid> (db/schema.sql applied, hand-made
fixture, dropped first and at the end). Tests 9 to 11 run the live CLIs as subprocesses against
the live database kalbt (rolled back or SELECT only) and compare live row counts before and after.

Fixture
  station KTST (offset -5), underlying U1, series S1.
  Each event has six markets <event>-A .. <event>-F, open the day before the event date 15:00 UTC.
  E1 event 2026-06-10, close 2026-06-11 05:00 UTC -> decision (H 15) 2026-06-10 14:00 UTC
  E2 event 2026-06-11, close 2026-06-12 04:59 UTC -> decision 2026-06-11 14:00 UTC
  E3 event 2026-06-12, close 2026-06-13 05:00 UTC -> decision 2026-06-12 14:00 UTC
  Candles of E1 (UTC, bid/ask) and, shifted by one day, of E2:
    A 13:30 0.60/0.64 (mid 0.62)   B 13:59 0.70/0.74 (mid 0.72)   C 12:30 0.90/0.94 (older than 1 h)
    D 14:01 0.96/0.98 (after the decision)   E 13:50 NULL/0.10 (no bid)   F none
  E3: A and B both 13:59 0.70/0.74 (tie).
"""
import os
import subprocess
import traceback
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from os import getpid
from pathlib import Path

from db.connect import connect
from strategies.midday_fade import (
    DEFAULTS,
    decision_time,
    intents_for_event,
    intents_for_series,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = REPO_ROOT / "db" / "schema.sql"
TEST_DB = f"kalbt_b021_test_{getpid()}"
LIVE_TABLES = ["run", "order_intent", "order_outcome", "settlement"]

D = Decimal
failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_test(name, func, *args):
    try:
        func(*args)
    except Exception:  # noqa: BLE001 - a failed test is reported, the rest still run
        report(name, False, traceback.format_exc())
    else:
        report(name, True)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def at(day, hour, minute=0):
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def add_event(cur, ticker, event_date, close, candles):
    """candles: {suffix: (hour, minute, bid, ask)} on the event date (UTC)."""
    cur.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                " VALUES (%s, 'S1', 'U1', %s, 'NWS')", (ticker, event_date))
    opens = at(event_date - timedelta(days=1), 15)
    for suffix in "ABCDEF":
        cur.execute("INSERT INTO market (ticker, event_ticker, strike_type, open_time, close_time)"
                    " VALUES (%s, %s, 'between', %s, %s)", (f"{ticker}-{suffix}", ticker, opens, close))
    for suffix, (hour, minute, bid, ask) in candles.items():
        cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                    " VALUES (%s, %s, %s, %s, 1)",
                    (f"{ticker}-{suffix}", at(event_date, hour, minute),
                     None if bid is None else D(bid), D(ask)))


LADDER = {"A": (13, 30, "0.60", "0.64"), "B": (13, 59, "0.70", "0.74"), "C": (12, 30, "0.90", "0.94"),
          "D": (14, 1, "0.96", "0.98"), "E": (13, 50, None, "0.10")}
TIE = {"A": (13, 59, "0.70", "0.74"), "B": (13, 59, "0.70", "0.74")}


def load_fixture(conn):
    cur = conn.cursor()
    cur.execute("INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h,"
                " iem_id, iem_network) VALUES ('KTST', 'Test KTST', 40, -74, 'America/New_York', -5,"
                " 'TST', 'NY_ASOS')")
    cur.execute("INSERT INTO underlying (underlying_id, description, station_id) VALUES ('U1', 'Test city', 'KTST')")
    cur.execute("INSERT INTO series (ticker, underlying_id, title) VALUES ('S1', 'U1', 'Test series')")
    add_event(cur, "E1", date(2026, 6, 10), at(date(2026, 6, 11), 5), LADDER)
    add_event(cur, "E2", date(2026, 6, 11), at(date(2026, 6, 12), 4, 59), LADDER)
    add_event(cur, "E3", date(2026, 6, 12), at(date(2026, 6, 13), 5), TIE)
    conn.commit()


# ---------------------------------------------------------------------------
# Agreed tests 1 to 8 (throwaway database)
# ---------------------------------------------------------------------------

def test_01_basic_fade(conn):
    intents, info = intents_for_event(conn, "E1", dict(DEFAULTS))
    check(len(intents) == 1, f"expected 1 intent, got {len(intents)}")
    intent = intents[0]
    check(intent["decided_at"] == at(date(2026, 6, 10), 14), f"decided_at {intent['decided_at']}")
    check(intent["market_ticker"] == "E1-B", f"market {intent['market_ticker']}")
    check(intent["side"] == "no" and intent["action"] == "buy", f"{intent['side']} {intent['action']}")
    check(intent["quantity"] == 10, f"quantity {intent['quantity']}")
    check(intent["reason"] == "fade favourite mid 0.720 at 15h before close", f"reason {intent['reason']!r}")
    check(info["favourite_mid"] == D("0.72"), f"favourite_mid {info['favourite_mid']}")
    check(info["skipped"] is None, f"skipped {info['skipped']}")


def test_02_close_rounded_up(conn):
    intents, info = intents_for_event(conn, "E2", dict(DEFAULTS))
    check(info["decided_at"] == at(date(2026, 6, 11), 14), f"info decided_at {info['decided_at']}")
    check(len(intents) == 1, f"expected 1 intent, got {len(intents)}")
    check(intents[0]["decided_at"] == at(date(2026, 6, 11), 14), f"decided_at {intents[0]['decided_at']}")
    check(intents[0]["market_ticker"] == "E2-B", f"market {intents[0]['market_ticker']}")


def test_03_min_yes_too_high(conn):
    intents, info = intents_for_event(conn, "E1", dict(DEFAULTS, min_yes="0.80"))
    check(intents == [], f"intents {intents}")
    check(info["skipped"] == "no_favourite", f"skipped {info['skipped']!r}")


def test_04_max_yes_excludes_b(conn):
    intents, info = intents_for_event(conn, "E1", dict(DEFAULTS, max_yes="0.70"))
    check(len(intents) == 1, f"expected 1 intent, got {len(intents)}")
    check(intents[0]["market_ticker"] == "E1-A", f"market {intents[0]['market_ticker']}")
    check(info["favourite_mid"] == D("0.62"), f"favourite_mid {info['favourite_mid']}")


def test_05_no_quotes(conn):
    intents, info = intents_for_event(conn, "E1", dict(DEFAULTS, hours_before_close=20))
    check(info["decided_at"] == at(date(2026, 6, 10), 9), f"decided_at {info['decided_at']}")
    check(intents == [], f"intents {intents}")
    check(info["skipped"] == "no_quotes", f"skipped {info['skipped']!r}")


def test_06_tie_keeps_first_ticker(conn):
    intents, info = intents_for_event(conn, "E3", dict(DEFAULTS))
    check(len(intents) == 1, f"expected 1 intent, got {len(intents)}")
    check(intents[0]["market_ticker"] == "E3-A", f"market {intents[0]['market_ticker']}")
    check(info["favourite_market"] == "E3-A", f"favourite_market {info['favourite_market']}")


def test_07_series(conn):
    intents, infos = intents_for_series(conn, "S1", date(2026, 6, 10), date(2026, 6, 12), dict(DEFAULTS))
    tickers = [info["event_ticker"] for info in infos]
    check(tickers == ["E1", "E2", "E3"], f"infos {tickers}")
    check(len(intents) == 3, f"expected 3 intents, got {len(intents)}")
    check([i["market_ticker"] for i in intents] == ["E1-B", "E2-B", "E3-A"],
          f"intent markets {[i['market_ticker'] for i in intents]}")


def test_08_decision_time():
    got = decision_time(at(date(2026, 6, 11), 5), 15)
    check(got == at(date(2026, 6, 10), 14), f"05:00 H15 gave {got}")
    got = decision_time(at(date(2026, 6, 11), 4, 59), 15)
    check(got == at(date(2026, 6, 10), 14), f"04:59 H15 gave {got}")
    got = decision_time(at(date(2026, 6, 11), 5, 30), 6)
    check(got == at(date(2026, 6, 11), 0), f"05:30 H6 gave {got}")


# ---------------------------------------------------------------------------
# Agreed tests 9 to 11 (live CLIs)
# ---------------------------------------------------------------------------

def row_counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in LIVE_TABLES}


def cli(*args):
    return subprocess.run(["uv", "run", "python", *args], check=False, cwd=REPO_ROOT, capture_output=True, text=True)


def tail(result):
    return f"exit {result.returncode}\nstdout: {result.stdout[-800:]}\nstderr: {result.stderr[-800:]}"


def test_09_live_run_rolled_back(live_conn):
    before = row_counts(live_conn)
    live_conn.commit()
    result = cli("-m", "backtest.run_all", "--db", "kalbt", "--strategy", "midday_fade", "--series", "KXHIGHNY")
    check(result.returncode == 0, tail(result))
    check("recon PASS" in result.stdout, "no 'recon PASS' in output\n" + tail(result))
    check("not saved" in result.stdout, "no 'not saved' in output\n" + tail(result))
    after = row_counts(live_conn)
    live_conn.commit()
    check(after == before, f"live tables changed: {before} -> {after}")


def test_10_existing_suites():
    result = cli("-m", "backtest.run_all", "--db", "kalbt", "--dry")
    check(result.returncode == 0, "default --dry: " + tail(result))
    env_cmd = ["uv", "run", "python"]
    for script in ("tests/backtest/check_run_all.py", "tests/backtest/check_forecast_favourite.py"):
        proc = subprocess.run(env_cmd + [script], check=False, cwd=REPO_ROOT, capture_output=True, text=True,
                              env={**os.environ, "PYTHONPATH": "."})
        check(proc.returncode == 0, f"{script}: {tail(proc)}")


def test_11_strategy_cli():
    result = cli("-m", "strategies.midday_fade", "--series", "KXHIGHNY", "--start", "2026-11-16",
                 "--end", "2026-11-22")
    check(result.returncode == 0, tail(result))
    event_lines = [line for line in result.stdout.splitlines() if line.startswith("KXHIGHNY-")]
    check(len(event_lines) == 7, f"expected 7 event lines, got {len(event_lines)}\n{result.stdout}")


def main():
    live_conn = connect()
    admin = connect()
    admin.autocommit = True
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    conn = None
    try:
        conn = connect(dbname=TEST_DB)
        conn.execute(SCHEMA.read_text())
        conn.commit()
        load_fixture(conn)

        run_test("01 E1: one buy-NO intent on E1-B, mid 0.720", test_01_basic_fade, conn)
        run_test("02 E2: close 04:59 rounds up, decision 14:00, E2-B", test_02_close_rounded_up, conn)
        run_test("03 min_yes 0.80 skips no_favourite", test_03_min_yes_too_high, conn)
        run_test("04 max_yes 0.70 picks E1-A (mid 0.62)", test_04_max_yes_excludes_b, conn)
        run_test("05 hours_before_close 20 skips no_quotes", test_05_no_quotes, conn)
        run_test("06 tie keeps the first ticker", test_06_tie_keeps_first_ticker, conn)
        run_test("07 series: 3 infos in date order, 3 intents", test_07_series, conn)
        run_test("08 decision_time pure cases", test_08_decision_time)
        run_test("09 live run_all --strategy midday_fade rolled back, counts unchanged", test_09_live_run_rolled_back, live_conn)
        run_test("10 default --dry and existing suites still pass", test_10_existing_suites)
        run_test("11 strategy CLI prints 7 event lines", test_11_strategy_cli)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.close()
        live_conn.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
