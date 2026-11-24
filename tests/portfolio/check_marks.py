"""Checks the hourly equity curve (spec C11, agreed tests 1 to 13).

Creates the throwaway database kalbt_marks_test_<pid> (schema + portfolio views + recon fixture),
builds one hand run in it, and drops the database first and at the end (also on failure).
Tests 12 and 13 read the live kalbt database with SELECT only (run 32) and compare row counts.
Expected values are the hand values of the spec.
Run from the repo root: PYTHONPATH=. uv run python tests/portfolio/check_marks.py
"""
import os
import subprocess
import sys
import time
import traceback
from datetime import date, timedelta
from itertools import pairwise
from pathlib import Path

from db.connect import connect
from portfolio.marks import equity_curve
from tests.recon.check_check_run import M1, M2, D, at, filled, hand_run, load_fixture

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = f"kalbt_marks_test_{os.getpid()}"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
LIVE_RUN = 32

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_test(name, function, ctx):
    try:
        function(ctx)
    except Exception as error:  # noqa: BLE001 - a failed test is reported, the rest still run
        traceback.print_exc()
        report(name, False, f"{type(error).__name__}: {error}")
    else:
        report(name, True)


def row_counts(conn):
    cur = conn.cursor()
    counts = {}
    for table in LIVE_TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


def build_run(conn):
    run_id = hand_run(
        conn,
        [(at(5, 31, 16, 0), M1, "yes", "buy", 5, [filled(5, "0.45", "0.09", at(5, 31, 16, 1))]),
         (at(6, 1, 2, 0), M2, "no", "buy", 10, [filled(10, "0.55", "0.18", at(6, 1, 2, 1))])],
        snapshots=[(date(2026, 6, 1), M1, D(5), D("0.45")), (date(2026, 6, 1), M2, D(-10), D("0.45"))],
        settlements=[(M1, "yes", D(5), D(5)), (M2, "no", D(-10), D(10))])
    cur = conn.cursor()
    cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                " VALUES (%s, %s, NULL, 0.60, 0)", (M1, at(6, 1, 22, 0)))
    cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                " VALUES (%s, %s, 0.98, NULL, 0)", (M2, at(6, 2, 1, 30)))
    conn.commit()
    return run_id


def row_at(ctx, ts):
    found = [r for r in ctx["curve"] if r["ts"] == ts]
    assert len(found) == 1, f"{len(found)} rows at {ts}"
    return found[0]


def expect(row, **values):
    for key, want in values.items():
        got = row[key]
        if key == "open_markets":
            assert got == want, f"{row['ts']} {key}: {got}, expected {want}"
        else:
            assert round(got, 2) == D(want), f"{row['ts']} {key}: {got}, expected {want}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_01_rows_and_hours(ctx):
    curve = equity_curve(ctx["conn"], ctx["run"])
    ctx["curve"] = curve
    assert len(curve) == 46, f"{len(curve)} rows, expected 46"
    assert curve[0]["ts"] == at(5, 31, 16, 0), f"first ts {curve[0]['ts']}"
    assert curve[-1]["ts"] == at(6, 2, 13, 0), f"last ts {curve[-1]['ts']}"
    for a, b in pairwise(curve):
        assert b["ts"] - a["ts"] == timedelta(hours=1), f"gap between {a['ts']} and {b['ts']}"
    expected_keys = {"ts", "cash", "positions_value", "cost_basis_open", "realized_pnl_net",
                     "unrealized_pnl", "equity", "open_markets"}
    assert all(set(r) == expected_keys for r in curve), "keys differ"
    assert all(r["ts"].utcoffset() == timedelta(0) for r in curve), "ts not UTC"


def test_02_first_hour(ctx):
    expect(row_at(ctx, at(5, 31, 16, 0)), cash="1000", positions_value="0", equity="1000",
           open_markets=0)


def test_03_after_first_fill(ctx):
    expect(row_at(ctx, at(5, 31, 17, 0)), cash="997.66", positions_value="2.00",
           cost_basis_open="2.25", unrealized_pnl="-0.25", realized_pnl_net="-0.09",
           equity="999.66", open_markets=1)


def test_04_both_open(ctx):
    expect(row_at(ctx, at(6, 1, 3, 0)), cash="991.98", positions_value="7.30",
           cost_basis_open="7.75", unrealized_pnl="-0.45", realized_pnl_net="-0.27",
           equity="999.28", open_markets=2)


def test_05_bid_from_10_11(ctx):
    expect(row_at(ctx, at(6, 1, 11, 0)), positions_value="7.50", equity="999.48")


def test_06_bid_from_20_00(ctx):
    expect(row_at(ctx, at(6, 1, 21, 0)), positions_value="7.80", equity="999.78")


def test_07_null_bid_marks_zero(ctx):
    expect(row_at(ctx, at(6, 1, 23, 0)), positions_value="5.30", equity="997.28")


def test_08_null_ask_marks_zero(ctx):
    expect(row_at(ctx, at(6, 2, 2, 0)), positions_value="0", equity="991.98", open_markets=2)


def test_09_after_settlement(ctx):
    for hour in (12, 13):
        expect(row_at(ctx, at(6, 2, hour, 0)), cash="1006.98", positions_value="0",
               cost_basis_open="0", realized_pnl_net="6.98", equity="1006.98", open_markets=0)


def test_10_identities_exact(ctx):
    for r in ctx["curve"]:
        assert r["cash"] + r["cost_basis_open"] == 1000 + r["realized_pnl_net"], \
            f"{r['ts']}: cash + cost_basis_open != 1000 + realized_pnl_net"
        assert r["equity"] == 1000 + r["realized_pnl_net"] + r["unrealized_pnl"], \
            f"{r['ts']}: equity identity broken"


def test_11_no_fills_empty(ctx):
    run_id = hand_run(ctx["conn"], [])
    assert equity_curve(ctx["conn"], run_id) == []


def test_12_live_run(ctx):
    live = connect()
    try:
        before = row_counts(live)
        start = time.monotonic()
        curve = equity_curve(live, LIVE_RUN)
        elapsed = time.monotonic() - start
        assert elapsed < 10, f"took {elapsed:.1f} s"
        assert curve, "empty curve"
        assert curve[-1]["open_markets"] == 0, f"open_markets {curve[-1]['open_markets']}"
        cur = live.cursor()
        cur.execute("SELECT net_pnl_cum FROM portfolio_daily WHERE run_id = %s ORDER BY day DESC LIMIT 1",
                    (LIVE_RUN,))
        net_pnl_cum = cur.fetchone()[0]
        assert curve[-1]["equity"] == 1000 + net_pnl_cum, \
            f"equity {curve[-1]['equity']}, expected {1000 + net_pnl_cum}"
        live.rollback()
        after = row_counts(live)
        assert after == before, f"live row counts: before {before}, after {after}"
    finally:
        live.close()


def test_13_cli(ctx):
    result = subprocess.run([sys.executable, "-m", "portfolio.marks", str(LIVE_RUN)], check=False, cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT)},
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, f"exit {result.returncode}: {result.stderr}"
    assert result.stdout.strip(), "no output"
    assert len(result.stdout.strip().splitlines()) >= 1


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    admin = connect()
    admin.autocommit = True
    admin.execute(f"DROP DATABASE IF EXISTS {DB}")
    admin.execute(f"CREATE DATABASE {DB}")
    conn = None
    try:
        conn = connect(dbname=DB)
        conn.execute(SCHEMA.read_text())
        conn.execute(PORTFOLIO.read_text())
        conn.commit()
        load_fixture(conn)
        ctx = {"conn": conn, "run": build_run(conn)}

        run_test("01 46 hourly rows, first and last ts", test_01_rows_and_hours, ctx)
        if "curve" in ctx:
            run_test("02 first hour", test_02_first_hour, ctx)
            run_test("03 after the first fill", test_03_after_first_fill, ctx)
            run_test("04 both markets open", test_04_both_open, ctx)
            run_test("05 M1 at the 10:11 bid", test_05_bid_from_10_11, ctx)
            run_test("06 M1 at the 20:00 bid", test_06_bid_from_20_00, ctx)
            run_test("07 NULL bid marks 0", test_07_null_bid_marks_zero, ctx)
            run_test("08 NULL ask marks 0", test_08_null_ask_marks_zero, ctx)
            run_test("09 after settlement", test_09_after_settlement, ctx)
            run_test("10 identities hold exactly", test_10_identities_exact, ctx)
        else:
            report("02-10 hand-value checks", False, "test 1 gave no curve")
        run_test("11 run with no fills gives []", test_11_no_fills_empty, ctx)
        run_test("12 live run 32, fast, ends flat, read-only", test_12_live_run, ctx)
        run_test("13 CLI on run 32", test_13_cli, ctx)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {DB}")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
