"""Checks the run metrics (spec C12, agreed tests 1 to 10).

Tests 1 to 6 are pure (hand numbers). Tests 7 to 9 use the throwaway database
kalbt_metrics_test_<pid> (schema + portfolio views + recon fixture, one hand run), dropped first and
at the end (also on failure). Test 10 reads the live kalbt database with SELECT only (run 32).
Expected values are the hand values of the spec.
Run from the repo root: PYTHONPATH=. uv run python tests/portfolio/check_metrics.py
"""
import os
import subprocess
import sys
import time
import traceback
from datetime import date
from pathlib import Path

from db.connect import connect
from portfolio.metrics import (
    daily_points,
    event_risk,
    historical_var,
    metrics,
    run_metrics,
    trades,
    worst_loss,
)
from tests.recon.check_check_run import M1, M2, D, at, filled, hand_run, load_fixture

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = f"kalbt_metrics_test_{os.getpid()}"
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


def curve_row(ts, equity, open_markets, cost_basis_open):
    return {"ts": ts, "equity": D(equity), "open_markets": open_markets,
            "cost_basis_open": D(cost_basis_open)}


def hand_curve():
    return [curve_row(at(6, 1, 0), 1000, 0, 0),
            curve_row(at(6, 1, 12), 990, 1, 5),
            curve_row(at(6, 2, 0), 1010, 1, 5),
            curve_row(at(6, 2, 6), 980, 1, 5),
            curve_row(at(6, 3, 0), 1000, 1, 5),
            curve_row(at(6, 3, 5), 1005, 0, 0)]


def close(got, want, tolerance, key):
    assert got is not None and abs(got - want) <= tolerance, f"{key}: {got}, expected {want}"


def same(got, want, key):
    assert round(got, 2) == D(want), f"{key}: {got}, expected {want}"


# ---------------------------------------------------------------------------
# Pure tests
# ---------------------------------------------------------------------------

def test_01_daily_points(ctx):
    points = daily_points(hand_curve())
    assert len(points) == 3, f"{len(points)} points, expected 3"
    want = [(date(2026, 6, 1), "10", 0.01), (date(2026, 6, 2), "-10", -0.0099010),
            (date(2026, 6, 3), "5", 0.005)]
    for point, (day, pnl, ret) in zip(points, want):
        assert point["day"] == day, f"day {point['day']}, expected {day}"
        assert point["pnl"] == D(pnl), f"{day} pnl {point['pnl']}, expected {pnl}"
        assert round(point["ret"], 6) == round(ret, 6), f"{day} ret {point['ret']}, expected {ret}"


def test_02_metrics_hand_curve(ctx):
    trade_rows = [
        {"pnl_net": D("2.66"), "fees": D("0.09"), "open": False},
        {"pnl_net": D("4.32"), "fees": D("0.18"), "open": False},
        {"pnl_net": D("-1.00"), "fees": D("0.05"), "open": False},
        {"pnl_net": D("0"), "fees": D("0.02"), "open": True}]
    m = metrics(hand_curve(), trade_rows, D("7.75"), D("7.75"))
    same(m["starting_cash"], "1000", "starting_cash")
    same(m["final_equity"], "1005", "final_equity")
    same(m["total_pnl"], "5", "total_pnl")
    close(m["total_return"], 0.005, 1e-6, "total_return")
    same(m["max_drawdown"], "30", "max_drawdown")
    close(m["max_drawdown_pct"], 0.029703, 1e-6, "max_drawdown_pct")
    assert m["max_drawdown_at"] == at(6, 2, 6), f"max_drawdown_at {m['max_drawdown_at']}"
    assert m["n_days"] == 3, f"n_days {m['n_days']}"
    close(m["sharpe_daily"], 3.13654, 1e-4, "sharpe_daily")
    same(m["var_95"], "10", "var_95")
    same(m["var_99"], "10", "var_99")
    assert m["n_trades"] == 4, f"n_trades {m['n_trades']}"
    assert m["n_open"] == 1, f"n_open {m['n_open']}"
    close(m["win_rate"], 0.666667, 1e-6, "win_rate")
    same(m["avg_win"], "3.49", "avg_win")
    same(m["avg_loss"], "-1.00", "avg_loss")
    same(m["profit_factor"], "6.98", "profit_factor")
    close(float(m["expectancy"]), 1.993333, 1e-6, "expectancy")
    close(m["sharpe_per_trade"], 0.732322, 1e-5, "sharpe_per_trade")
    same(m["fees"], "0.34", "fees")
    same(m["max_cost_basis_open"], "5", "max_cost_basis_open")
    assert m["max_open_markets"] == 1, f"max_open_markets {m['max_open_markets']}"
    close(m["time_in_market"], 0.666667, 1e-6, "time_in_market")
    same(m["peak_capital_at_risk"], "7.75", "peak_capital_at_risk")
    same(m["max_event_worst_loss"], "7.75", "max_event_worst_loss")


def test_03_historical_var(ctx):
    pnls = [D(i - 50) for i in range(100)]
    assert historical_var(pnls, 0.95) == D(45), f"var_95 {historical_var(pnls, 0.95)}"
    assert historical_var(pnls, 0.99) == D(49), f"var_99 {historical_var(pnls, 0.99)}"


def test_04_worst_loss_two_shorts(ctx):
    got = worst_loss(["A", "B", "C"], {"A": D(-10), "B": D(-10)}, {"A": D("5.00"), "B": D("6.00")})
    assert got == D("1.00"), f"worst_loss {got}"


def test_05_worst_loss_long_and_mixed(ctx):
    got = worst_loss(["A", "B", "C"], {"A": D(5)}, {"A": D("2.25")})
    assert got == D("2.25"), f"worst_loss long {got}"
    got = worst_loss(["A", "B", "C"], {"A": D(5), "B": D(-10)}, {"A": D("2.25"), "B": D("5.50")})
    assert got == D("7.75"), f"worst_loss mixed {got}"


def test_06_empty_curve(ctx):
    assert daily_points([]) == []
    m = metrics([], [], 0, 0)
    keys = ["starting_cash", "final_equity", "total_pnl", "total_return", "max_drawdown",
            "max_drawdown_pct", "max_drawdown_at", "n_days", "sharpe_daily", "var_95", "var_99",
            "n_trades", "n_open", "win_rate", "avg_win", "avg_loss", "profit_factor", "expectancy",
            "sharpe_per_trade", "fees", "max_cost_basis_open", "max_open_markets", "time_in_market",
            "peak_capital_at_risk", "max_event_worst_loss"]
    assert set(m) == set(keys), f"key difference: {set(m) ^ set(keys)}"
    for key in ("n_days", "n_trades", "n_open", "max_open_markets", "fees",
                "peak_capital_at_risk", "max_event_worst_loss"):
        assert m[key] == 0, f"{key}: {m[key]}, expected 0"
    for key in keys:
        if key not in ("n_days", "n_trades", "n_open", "max_open_markets", "fees",
                       "peak_capital_at_risk", "max_event_worst_loss"):
            assert m[key] is None, f"{key}: {m[key]}, expected None"


# ---------------------------------------------------------------------------
# Database tests
# ---------------------------------------------------------------------------

def test_07_trades(ctx):
    rows = trades(ctx["conn"], ctx["run"])
    assert len(rows) == 2, f"{len(rows)} rows, expected 2"
    by_market = {r["market_ticker"]: r for r in rows}
    assert set(by_market) == {M1, M2}, f"markets {set(by_market)}"
    for row in rows:
        assert row["event_ticker"] == "EV-0601", f"event {row['event_ticker']}"
        assert row["underlying_id"] == "U1", f"underlying {row['underlying_id']}"
        assert row["event_date"] == date(2026, 6, 1), f"event_date {row['event_date']}"
        assert row["open"] is False, f"{row['market_ticker']} open"
    assert by_market[M1]["n_fills"] == 1, f"n_fills {by_market[M1]['n_fills']}"
    same(by_market[M1]["fees"], "0.09", "M1 fees")
    same(by_market[M1]["pnl_net"], "2.66", "M1 pnl_net")
    same(by_market[M2]["fees"], "0.18", "M2 fees")
    same(by_market[M2]["pnl_net"], "4.32", "M2 pnl_net")


def test_08_event_risk(ctx):
    rows, peak = event_risk(ctx["conn"], ctx["run"])
    assert len(rows) == 1, f"{len(rows)} rows, expected 1"
    assert rows[0]["event_ticker"] == "EV-0601", f"event {rows[0]['event_ticker']}"
    same(rows[0]["max_worst_loss"], "7.75", "max_worst_loss")
    same(peak, "7.75", "peak_capital_at_risk")


def test_09_run_metrics(ctx):
    m = run_metrics(ctx["conn"], ctx["run"])
    same(m["total_pnl"], "6.98", "total_pnl")
    same(m["final_equity"], "1006.98", "final_equity")
    assert m["n_trades"] == 2, f"n_trades {m['n_trades']}"
    assert m["win_rate"] == 1.0, f"win_rate {m['win_rate']}"
    assert m["profit_factor"] is None, f"profit_factor {m['profit_factor']}"
    same(m["fees"], "0.27", "fees")
    assert m["max_open_markets"] == 2, f"max_open_markets {m['max_open_markets']}"
    same(m["max_cost_basis_open"], "7.75", "max_cost_basis_open")


def test_10_live_run_and_cli(ctx):
    live = connect()
    try:
        before = row_counts(live)
        start = time.monotonic()
        result = run_metrics(live, LIVE_RUN)
        elapsed = time.monotonic() - start
        assert elapsed < 15, f"took {elapsed:.1f} s"
        cur = live.cursor()
        cur.execute("SELECT net_pnl_cum FROM portfolio_daily WHERE run_id = %s ORDER BY day DESC LIMIT 1",
                    (LIVE_RUN,))
        net_pnl_cum = cur.fetchone()[0]
        assert result["total_pnl"] == net_pnl_cum, f"total_pnl {result['total_pnl']}, expected {net_pnl_cum}"
        trade_sum = sum((t["pnl_net"] for t in trades(live, LIVE_RUN)), D(0))
        assert trade_sum == net_pnl_cum, f"sum of trade pnl_net {trade_sum}, expected {net_pnl_cum}"
        live.rollback()
        after = row_counts(live)
        assert after == before, f"live row counts: before {before}, after {after}"
    finally:
        live.close()
    cli = subprocess.run([sys.executable, "-m", "portfolio.metrics", str(LIVE_RUN)], check=False, cwd=ROOT,
                         env={**os.environ, "PYTHONPATH": str(ROOT)},
                         capture_output=True, text=True, timeout=120)
    assert cli.returncode == 0, f"CLI exit {cli.returncode}: {cli.stderr}"
    assert cli.stdout.strip(), "CLI printed nothing"


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

        run_test("01 daily_points on the hand curve", test_01_daily_points, ctx)
        run_test("02 metrics on the hand curve", test_02_metrics_hand_curve, ctx)
        run_test("03 historical_var 95 and 99", test_03_historical_var, ctx)
        run_test("04 worst_loss two shorts", test_04_worst_loss_two_shorts, ctx)
        run_test("05 worst_loss long and mixed", test_05_worst_loss_long_and_mixed, ctx)
        run_test("06 empty curve", test_06_empty_curve, ctx)
        run_test("07 trades", test_07_trades, ctx)
        run_test("08 event_risk", test_08_event_risk, ctx)
        run_test("09 run_metrics on the hand run", test_09_run_metrics, ctx)
        run_test("10 live run 32 and CLI, read-only", test_10_live_run_and_cli, ctx)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {DB}")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
