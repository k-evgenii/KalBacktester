"""Checks backtest/compare_kalshi.py (spec B015, agreed tests 1 to 8, plus extras).

Test 1 reads the live run 31 (SELECT only). Tests 2 to 7 use a throwaway database
kalbt_b015_test (schema applied, one tiny fixture, engine run to produce fills) and a temporary
dump folder written here. The throwaway database is dropped in a finally. The live database
kalbt is never written; the real dump folders are never modified.

Fixture: station KTST, event EV-0601 (2026-06-01) with markets M1 and M2. The nws_cli high is
3:00 PM at offset -5 (lock 20:00 UTC), so nothing locks early in the day.
Candles on both markets (UTC, 2026-06-01):
  01:30 vol 60.4, 02:00 vol 66.0, 02:01 bid 0.45 ask 0.47 vol 0
Window (01:00, 02:00] holds 126.4 -> cap floor(12.64) = 12.
Run 1 buys 12 yes on M1 decided 02:00: fills 12 at 0.47 at 02:01, fee
  ceil(0.07 * 12 * 0.47 * 0.53 * 100) / 100 = ceil(20.9244) / 100 = 0.21.
Run 2 buys 12 yes on M2 the same way (its market has no dump file).
"""
import json
import subprocess
import sys
import tempfile
import traceback
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import psycopg

from backtest.compare_kalshi import compare_run
from db.connect import connect, read_password
from engine.match_intents import match_intents

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
REPORTS = ROOT / "backtest" / "reports"
TEST_DB = "kalbt_b015_test"
TABLES = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]
LIVE_DUMPS = [
    Path("/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY"),
    Path("/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-06-01_2026-06-07"),
    Path("/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-08-12_2026-08-18"),
]

D = Decimal
M1, M2 = "T-M1", "T-M2"
FILL_AT = datetime(2026, 6, 1, 2, 1, tzinfo=timezone.utc)
FILL_TS = int(FILL_AT.timestamp())

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def at(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def live_counts():
    with connect() as conn:
        cur = conn.cursor()
        counts = {}
        for table in TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
    return counts


# ---------------------------------------------------------------------------
# Throwaway database and fixture
# ---------------------------------------------------------------------------

def admin_connection():
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="kalbt", user="kalbt",
                           password=read_password(), autocommit=True)


def create_test_database():
    with admin_connection() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"CREATE DATABASE {TEST_DB}")


def drop_test_database():
    with admin_connection() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")


def load_fixture(conn):
    cur = conn.cursor()
    cur.execute(SCHEMA.read_text())
    cur.execute(
        "INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h,"
        " iem_id, iem_network) VALUES ('KTST', 'Test station', 40, -74, 'America/New_York', -5,"
        " 'TST', 'NY_ASOS')")
    cur.execute("INSERT INTO underlying (underlying_id, description, station_id)"
                " VALUES ('U1', 'Test city', 'KTST')")
    cur.execute("INSERT INTO series (ticker, underlying_id, title) VALUES ('S1', 'U1', 'Test series')")
    cur.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                " VALUES ('EV-0601', 'S1', 'U1', '2026-06-01', 'NWS')")
    for ticker in (M1, M2):
        cur.execute("INSERT INTO market (ticker, event_ticker, open_time, close_time)"
                    " VALUES (%s, 'EV-0601', %s, %s)", (ticker, at(5, 31, 15, 0), at(6, 2, 6, 0)))
        for ts, bid, ask, volume in [(at(6, 1, 1, 30), "0.45", "0.47", "60.4"),
                                     (at(6, 1, 2, 0), "0.45", "0.47", "66.0"),
                                     (at(6, 1, 2, 1), "0.45", "0.47", "0")]:
            cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                        " VALUES (%s, %s, %s, %s, %s)", (ticker, ts, D(bid), D(ask), D(volume)))
    cur.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst,"
                " low_f, issued_at, reference)"
                " VALUES ('KTST', '2026-06-01', 'nws_cli', 80, '300 PM', 60, %s, 'CLITEST')",
                (at(6, 2, 8, 0),))
    conn.commit()


def run_buy(conn, name, ticker, side="yes", action="buy", quantity=12):
    intents = [{"decided_at": at(6, 1, 2, 0), "market_ticker": ticker, "side": side,
                "action": action, "quantity": quantity, "reason": None}]
    return match_intents(conn, name, {}, intents)


# ---------------------------------------------------------------------------
# Temporary dump folder
# ---------------------------------------------------------------------------

def bar(end_ts, bid, ask, key="close_dollars"):
    return {"end_period_ts": end_ts,
            "yes_bid": {key: bid} if bid is not None else {},
            "yes_ask": {key: ask} if ask is not None else {}}


def default_bars(ask="0.4700", key="close_dollars"):
    """A bar before the fill minute, the bar in force at 02:01, and a later bar that must be ignored."""
    return [bar(FILL_TS - 60, "0.4000", "0.4200", key),
            bar(FILL_TS, "0.4500", ask, key),
            bar(FILL_TS + 60, "0.1000", "0.9000", key)]


def trade(created_time, count, yes_price):
    return {"created_time": created_time, "count_fp": count,
            "yes_price_dollars": yes_price, "no_price_dollars": str(D(1) - D(yes_price))}


def write_dump(folder, ticker, bars=None, trades=None):
    if bars is not None:
        (folder / "bars_1min").mkdir(parents=True, exist_ok=True)
        (folder / "bars_1min" / f"{ticker}.json").write_text(
            json.dumps({"ticker": ticker, "surface": "live", "candlesticks": bars}))
    if trades is not None:
        (folder / "trades").mkdir(parents=True, exist_ok=True)
        (folder / "trades" / f"{ticker}.json").write_text(
            json.dumps({"ticker": ticker, "surface": "live", "trades": trades}))


def fresh_dump(tmp, name, bars=None, trades=None, ticker=M1):
    folder = Path(tmp) / name
    folder.mkdir()
    write_dump(folder, ticker, bars, trades)
    return folder


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_01_live_run_31():
    with connect() as conn:
        result = compare_run(conn, 31, LIVE_DUMPS)
    fills = result["fills"]
    statuses = [f["quote"]["status"] for f in fills]
    assert result["verdict"] == "PASS", f"verdict {result['verdict']}, statuses {statuses}"
    assert len(fills) == 5, f"expected 5 fills, got {len(fills)}"
    assert all(s == "match" for s in statuses), f"statuses {statuses}"
    assert all(isinstance(f.get("trades"), dict) for f in fills), "a fill has no trades entry"
    assert all(isinstance(f.get("fee"), dict) for f in fills), "a fill has no fee entry"
    for f in fills:
        engine, formula = D(f["fee"]["engine"]), D(f["fee"]["formula_cents"])
        assert engine == formula, f"outcome {f['outcome_id']}: engine {engine}, formula {formula}"
    assert result["summary"]["fills"] == 5 and result["summary"]["fee_formula_equal"] == 5, result["summary"]


def test_02_mismatch_and_cli_exit_code(conn, ctx):
    folder = fresh_dump(ctx["tmp"], "t02", bars=default_bars(ask="0.4800"), trades=[])
    result = compare_run(conn, ctx["run1"], [folder])
    fill = result["fills"][0]
    assert fill["quote"]["status"] == "mismatch", fill["quote"]
    assert fill["quote"]["kalshi_price"] == "0.4800", fill["quote"]
    assert result["verdict"] == "FAIL", result["verdict"]
    report_file = REPORTS / f"compare_kalshi_run{ctx['run1']}.json"
    saved = report_file.read_bytes() if report_file.exists() else None
    try:
        done = subprocess.run(
            [sys.executable, "-m", "backtest.compare_kalshi", str(ctx["run1"]),
             "--db", TEST_DB, "--dump", str(folder)],
            cwd=ROOT, capture_output=True, text=True)
        assert done.returncode == 1, f"exit {done.returncode}\n{done.stdout}\n{done.stderr}"
        assert report_file.exists(), "the CLI wrote no report file"
        assert json.loads(report_file.read_text())["verdict"] == "FAIL"
    finally:
        if saved is None:
            report_file.unlink(missing_ok=True)
        else:
            report_file.write_bytes(saved)


def test_03_historical_close_key(conn, ctx):
    folder = fresh_dump(ctx["tmp"], "t03", bars=default_bars(key="close"), trades=[])
    result = compare_run(conn, ctx["run1"], [folder])
    quote = result["fills"][0]["quote"]
    assert quote["status"] == "match", quote
    assert D(quote["raw_ask"]) == D("0.47") and D(quote["raw_bid"]) == D("0.45"), quote
    assert result["verdict"] == "PASS", result["verdict"]


def test_04_raw_ask_one_is_no_ask(conn, ctx):
    folder = fresh_dump(ctx["tmp"], "t04", bars=default_bars(ask="1.0000"), trades=[])
    result = compare_run(conn, ctx["run1"], [folder])
    quote = result["fills"][0]["quote"]
    assert quote["status"] == "mismatch", quote
    assert quote["kalshi_price"] is None, quote
    assert result["verdict"] == "FAIL", result["verdict"]


def test_05_trades_window(conn, ctx):
    # occurred_at = 02:01:00. 02:00:00 is exactly 60 s before: excluded. 02:01:00 included.
    # 02:01:30 is after occurred_at: excluded. Included trade: 4 contracts at 0.50, our price 0.47
    # -> outside. If the excluded trades (0.40 and 0.60) leaked in, 0.47 would be inside.
    trades = [trade("2026-06-01T02:00:00Z", "5.00", "0.40"),
              trade("2026-06-01T02:01:00Z", "4.00", "0.50"),
              trade("2026-06-01T02:01:30Z", "7.00", "0.60")]
    folder = fresh_dump(ctx["tmp"], "t05", bars=default_bars(), trades=trades)
    info = compare_run(conn, ctx["run1"], [folder])["fills"][0]["trades"]
    assert info["n"] == 1, info
    assert D(info["volume"]) == D(4), info
    assert D(info["min_price"]) == D("0.50") and D(info["max_price"]) == D("0.50"), info
    assert info["position"] == "outside", info


def test_05b_trades_inside_and_none(conn, ctx):
    inside = [trade("2026-06-01T02:00:30Z", "2.00", "0.45"),
              trade("2026-06-01T02:01:00Z", "3.00", "0.49")]
    folder = fresh_dump(ctx["tmp"], "t05b", bars=default_bars(), trades=inside)
    info = compare_run(conn, ctx["run1"], [folder])["fills"][0]["trades"]
    assert info["n"] == 2 and D(info["volume"]) == D(5) and info["position"] == "inside", info
    only_old = [trade("2026-06-01T02:00:00Z", "5.00", "0.40")]
    folder = fresh_dump(ctx["tmp"], "t05c", bars=default_bars(), trades=only_old)
    info = compare_run(conn, ctx["run1"], [folder])["fills"][0]["trades"]
    assert info["n"] == 0 and info["position"] == "no_trades" and info["min_price"] is None, info
    folder = fresh_dump(ctx["tmp"], "t05d", bars=default_bars(), trades=None)
    result = compare_run(conn, ctx["run1"], [folder])
    assert result["fills"][0]["trades"]["position"] == "no_trades", result["fills"][0]["trades"]
    assert result["verdict"] == "PASS", "a missing trades file must not change the verdict"


def test_06_fee_values(conn, ctx):
    # 12 contracts at 0.47, rate 0.07: raw = 0.07 * 12 * 0.47 * 0.53 = 0.209244
    # formula_cents = ceil(20.9244) / 100 = 0.21, model_fee 0.209244, difference 0.21 - 0.209244 = 0.000756
    folder = fresh_dump(ctx["tmp"], "t06", bars=default_bars(), trades=[])
    result = compare_run(conn, ctx["run1"], [folder])
    fill = result["fills"][0]
    assert D(fill["quantity"]) == 12 and D(fill["price"]) == D("0.47"), fill
    fee = fill["fee"]
    assert D(fee["engine"]) == D("0.21"), fee
    assert D(fee["formula_cents"]) == D("0.21"), fee
    assert D(fee["model_fee"]) == D("0.209244"), fee
    assert D(fee["difference_to_model"]) == D("0.000756"), fee
    assert result["summary"]["fee_formula_equal"] == 1, result["summary"]


def test_07_no_dump_folder(conn, ctx):
    # M2 has no bars file in the dump folder (only M1 does)
    folder = fresh_dump(ctx["tmp"], "t07", bars=default_bars(), trades=[], ticker=M1)
    result = compare_run(conn, ctx["run2"], [folder])
    quote = result["fills"][0]["quote"]
    assert quote["status"] == "no_dump", quote
    assert result["verdict"] == "FAIL", result["verdict"]


def test_07b_no_bar_before_fill(conn, ctx):
    late = [bar(FILL_TS + 60, "0.4500", "0.4700")]
    folder = fresh_dump(ctx["tmp"], "t07b", bars=late, trades=[])
    result = compare_run(conn, ctx["run1"], [folder])
    assert result["fills"][0]["quote"]["status"] == "no_bar", result["fills"][0]["quote"]
    assert result["verdict"] == "FAIL", result["verdict"]


def test_08_read_only(conn, ctx):
    folder = fresh_dump(ctx["tmp"], "t08", bars=default_bars(), trades=[])
    with connect(dbname=TEST_DB) as ro:
        ro.read_only = True
        result = compare_run(ro, ctx["run1"], [folder])
    assert result["verdict"] == "PASS", result["verdict"]
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM run")
    assert cur.fetchone()[0] == 2, "compare_run changed the run table"


# ---------------------------------------------------------------------------

def run_test(test, *args):
    name = f"{test.__name__}"
    try:
        test(*args)
    except Exception:
        report(name, False, "\n" + traceback.format_exc())
    else:
        report(name, True)


def main():
    before = live_counts()
    run_test(test_01_live_run_31)
    try:
        create_test_database()
        with connect(dbname=TEST_DB) as conn, tempfile.TemporaryDirectory() as tmp:
            load_fixture(conn)
            ctx = {"tmp": tmp, "run1": run_buy(conn, "b015_m1", M1), "run2": run_buy(conn, "b015_m2", M2)}
            for test in [test_02_mismatch_and_cli_exit_code, test_03_historical_close_key,
                         test_04_raw_ask_one_is_no_ask, test_05_trades_window,
                         test_05b_trades_inside_and_none, test_06_fee_values,
                         test_07_no_dump_folder, test_07b_no_bar_before_fill, test_08_read_only]:
                run_test(test, conn, ctx)
    finally:
        drop_test_database()
    after = live_counts()
    report("test_08b_live_trading_tables_unchanged", before == after, f"before {before}, after {after}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
