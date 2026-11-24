"""Checks for spec B020 (backtest/delete_run.py), agreed tests 1 to 7, plus extras.

Run from the repo root: uv run python -m tests.backtest.check_delete_run
Creates the throwaway database kalbt_b020_test from db/schema.sql (plus db/portfolio.sql for the
portfolio views), loads the market and candle fixture of tests/engine/check_match_intents.py,
saves runs with match_intents + commit, and deletes them. The database is dropped at the end.
Live kalbt is only read: trading-table counts before and after (test 7).
"""
import subprocess
import sys
import traceback
from pathlib import Path

from backtest.delete_run import delete_runs
from db.connect import connect
from engine.match_intents import match_intents
from tests.engine.check_match_intents import M1, M2, M3, at, intent, load_fixture

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = REPO_ROOT / "db" / "schema.sql"
PORTFOLIO = REPO_ROOT / "db" / "portfolio.sql"
TEST_DB = "kalbt_b020_test"
TRADING = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]
DELETE_ORDER = ["order_outcome", "order_intent", "settlement", "position_snapshot", "run"]
DATA = ["market", "candle"]
UNKNOWN = 999999

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + str(detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def check_equal(actual, expected, what):
    assert actual == expected, f"{what}: expected {expected}, got {actual}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def table_counts(conn, tables):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables}


def run_counts(conn, run_id):
    """Rows of one run in each of the five trading tables."""
    counts = {}
    for table in ["order_intent", "settlement", "position_snapshot", "run"]:
        counts[table] = conn.execute(f"SELECT count(*) FROM {table} WHERE run_id = %s",
                                     (run_id,)).fetchone()[0]
    counts["order_outcome"] = conn.execute(
        "SELECT count(*) FROM order_outcome WHERE intent_id IN"
        " (SELECT intent_id FROM order_intent WHERE run_id = %s)", (run_id,)).fetchone()[0]
    return {t: counts[t] for t in DELETE_ORDER}


def save_run_a(conn):
    """Fills on M1, M2 and M3 at 16:00 (settlements for M1 and M2, snapshots for all three),
    plus a sell with nothing held on M1 at 12:00 (a rejected outcome)."""
    run_id = match_intents(conn, "b020_a", {}, [
        intent(at(6, 1, 12, 0), M1, "yes", "sell", 2),
        intent(at(6, 1, 16, 0), M1, "yes", "buy", 6),
        intent(at(6, 1, 16, 0), M2, "yes", "buy", 4),
        intent(at(6, 1, 16, 0), M3, "yes", "buy", 3)])
    conn.commit()
    return run_id


def save_run_b(conn):
    """Fills on M1 and M2 at 14:00, and the same buy-then-partial-sell on M1 at 10:00 and 10:10."""
    run_id = match_intents(conn, "b020_b", {}, [
        intent(at(6, 1, 10, 0), M1, "yes", "buy", 8),
        intent(at(6, 1, 10, 10), M1, "yes", "sell", 3),
        intent(at(6, 1, 14, 0), M2, "yes", "buy", 3)])
    conn.commit()
    return run_id


def cli(*args):
    return subprocess.run([sys.executable, "-m", "backtest.delete_run", *args],
                          cwd=REPO_ROOT, capture_output=True, text=True)


def view_rows(conn, view, run_id):
    return conn.execute(f"SELECT count(*) FROM {view} WHERE run_id = %s", (run_id,)).fetchone()[0]


def existing_views(conn):
    rows = conn.execute("SELECT viewname FROM pg_views WHERE schemaname = 'public'"
                        " AND viewname IN ('portfolio_ledger', 'portfolio_daily')").fetchall()
    return [r[0] for r in rows]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_01_delete_one_of_two(conn, ctx):
    run_a, run_b = save_run_a(conn), save_run_b(conn)
    before_a, before_b = run_counts(conn, run_a), run_counts(conn, run_b)
    # the fixture must give run A rows in every table, or the test proves nothing
    assert all(n > 0 for n in before_a.values()), f"run A lacks rows: {before_a}"
    assert all(n > 0 for n in before_b.values()), f"run B lacks rows: {before_b}"
    ctx["run_b"] = run_b
    ctx["before_b"] = before_b

    counts = delete_runs(conn, [run_a])
    conn.commit()
    check_equal(list(counts), DELETE_ORDER, "key order of the returned counts")
    check_equal(counts, before_a, "returned counts vs rows A had")
    check_equal(run_counts(conn, run_a), {t: 0 for t in DELETE_ORDER}, "rows of A left")
    check_equal(run_counts(conn, run_b), before_b, "rows of B")


def test_02_unknown_id_raises(conn, ctx):
    before = table_counts(conn, TRADING)
    try:
        delete_runs(conn, [UNKNOWN])
    except ValueError as error:
        assert str(UNKNOWN) in str(error), f"the error does not name the id: {error}"
    else:
        raise AssertionError("no ValueError for an unknown run id")
    conn.rollback()
    check_equal(table_counts(conn, TRADING), before, "trading counts after the failed call")


def test_03_known_and_unknown_is_all_or_nothing(conn, ctx):
    run_a = save_run_a(conn)
    before_a = run_counts(conn, run_a)
    before = table_counts(conn, TRADING)
    try:
        delete_runs(conn, [run_a, UNKNOWN])
    except ValueError as error:
        assert str(UNKNOWN) in str(error), f"the error does not name the id: {error}"
        assert str(run_a) not in str(error).replace(str(UNKNOWN), ""), f"the error names the known id: {error}"
    else:
        raise AssertionError("no ValueError for [known, unknown]")
    conn.rollback()
    check_equal(run_counts(conn, run_a), before_a, "rows of the known run")
    check_equal(table_counts(conn, TRADING), before, "trading counts")
    ctx["leftover_a"] = run_a


def test_04_portfolio_views_empty_after_delete(conn, ctx):
    views = existing_views(conn)
    if not views:
        print("[SKIP] 04 portfolio views do not exist in the test database")
        return
    run_a = ctx["leftover_a"]
    for view in views:
        assert view_rows(conn, view, run_a) > 0, f"{view} has no rows for run {run_a} before the delete"
    delete_runs(conn, [run_a])
    conn.commit()
    for view in views:
        check_equal(view_rows(conn, view, run_a), 0, f"{view} rows for the deleted run")
    # the other run is still visible
    for view in views:
        assert view_rows(conn, view, ctx["run_b"]) > 0, f"{view} lost run B"


def test_05_market_and_candle_untouched(conn, ctx):
    run_c = save_run_a(conn)
    before = table_counts(conn, DATA)
    check_equal(before, ctx["data_before"], "market/candle counts before any delete")
    delete_runs(conn, [run_c])
    conn.commit()
    check_equal(table_counts(conn, DATA), before, "market/candle counts after a delete")
    # and after a failed call
    try:
        delete_runs(conn, [UNKNOWN])
    except ValueError:
        pass
    conn.rollback()
    check_equal(table_counts(conn, DATA), before, "market/candle counts after a failed delete")


def test_06_cli(conn, ctx):
    # without --db: argparse error (exit 2, usage on stderr)
    run_id = save_run_a(conn)
    result = cli(str(run_id))
    assert result.returncode == 2, f"no --db: exit {result.returncode}, stderr {result.stderr[-300:]}"
    assert "--db" in result.stderr, f"no --db: stderr does not mention --db: {result.stderr[-300:]}"
    assert run_counts(conn, run_id)["run"] == 1, "run was deleted although --db was missing"

    # valid id: exit 0, per-table lines, closing line, run gone
    before = run_counts(conn, run_id)
    result = cli(str(run_id), "--db", TEST_DB)
    assert result.returncode == 0, f"valid id: exit {result.returncode}, stderr {result.stderr[-300:]}"
    lines = result.stdout.splitlines()
    for table in DELETE_ORDER:
        assert f"{table}: {before[table]}" in lines, f"missing line '{table}: {before[table]}' in {lines}"
    check_equal(lines[-1], f"deleted runs [{run_id}]", "last line")
    conn.rollback()  # fresh snapshot; the CLI committed in its own connection
    check_equal(run_counts(conn, run_id), {t: 0 for t in DELETE_ORDER}, "rows left after the CLI delete")

    # unknown id: exit 1, nothing deleted
    keep = save_run_b(conn)
    before_all = table_counts(conn, TRADING)
    result = cli(str(UNKNOWN), "--db", TEST_DB)
    assert result.returncode == 1, f"unknown id: exit {result.returncode}, stderr {result.stderr[-300:]}"
    assert str(UNKNOWN) in result.stdout + result.stderr, "unknown id: the error does not name the id"
    conn.rollback()
    check_equal(table_counts(conn, TRADING), before_all, "trading counts after the CLI failure")
    check_equal(run_counts(conn, keep)["run"], 1, "the saved run after the CLI failure")
    check_equal(table_counts(conn, DATA), ctx["data_before"], "market/candle counts after the CLI calls")


def test_e1_several_runs_in_one_call(conn, ctx):
    # extra: two runs in one call; returned counts are the sums
    run_a, run_b = save_run_a(conn), save_run_b(conn)
    a, b = run_counts(conn, run_a), run_counts(conn, run_b)
    counts = delete_runs(conn, [run_a, run_b])
    conn.commit()
    check_equal(counts, {t: a[t] + b[t] for t in DELETE_ORDER}, "returned counts")
    check_equal(run_counts(conn, run_a), {t: 0 for t in DELETE_ORDER}, "rows of A left")
    check_equal(run_counts(conn, run_b), {t: 0 for t in DELETE_ORDER}, "rows of B left")


def test_e2_does_not_commit(conn, ctx):
    # extra: the caller owns the transaction; after a rollback the run is back
    run_id = save_run_a(conn)
    before = run_counts(conn, run_id)
    delete_runs(conn, [run_id])
    check_equal(run_counts(conn, run_id), {t: 0 for t in DELETE_ORDER}, "rows visible to the same connection")
    other = connect(dbname=TEST_DB)
    try:
        check_equal(run_counts(other, run_id), before, "rows seen by another connection before commit")
    finally:
        other.close()
    conn.rollback()
    check_equal(run_counts(conn, run_id), before, "rows after rollback")
    delete_runs(conn, [run_id])
    conn.commit()


def test_07_live_untouched(live_before):
    live = connect()
    try:
        live_after = table_counts(live, TRADING)  # SELECT count(*) on the trading tables only
    finally:
        live.close()
    check_equal(live_after, live_before, "live trading-table counts")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_test(name, function, *args):
    try:
        function(*args)
    except Exception as error:
        traceback.print_exc()
        report(name, False, f"{type(error).__name__}: {error}")
        try:
            args[0].rollback()
        except Exception:
            pass
    else:
        report(name, True)


def main():
    live = connect()
    live_before = table_counts(live, TRADING)
    live.close()
    admin = connect()
    admin.autocommit = True
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    conn = None
    try:
        conn = connect(dbname=TEST_DB)
        conn.execute(SCHEMA.read_text())
        conn.commit()
        if not existing_views(conn):
            conn.execute(PORTFOLIO.read_text())  # the portfolio views (Track C)
            conn.commit()
        load_fixture(conn)
        ctx = {"data_before": table_counts(conn, DATA)}

        run_test("01 delete A of two saved runs: A gone, counts match, B unchanged", test_01_delete_one_of_two, conn, ctx)
        run_test("02 unknown run id raises ValueError, nothing deleted", test_02_unknown_id_raises, conn, ctx)
        run_test("03 [A, unknown] raises ValueError, A fully present", test_03_known_and_unknown_is_all_or_nothing, conn, ctx)
        run_test("04 portfolio views have no rows for a deleted run", test_04_portfolio_views_empty_after_delete, conn, ctx)
        run_test("05 market and candle counts unchanged", test_05_market_and_candle_untouched, conn, ctx)
        run_test("06 CLI: --db required, success output, unknown id exits 1", test_06_cli, conn, ctx)
        run_test("E1 (extra) several runs in one call", test_e1_several_runs_in_one_call, conn, ctx)
        run_test("E2 (extra) delete_runs does not commit", test_e2_does_not_commit, conn, ctx)
        run_test("07 live trading-table counts unchanged", test_07_live_untouched, live_before)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.close()

    print(f"\n{len(failures)} failed: {failures}" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
