"""Checks portfolio.check_run (spec C03, agreed tests 1 to 11, then extra attacks).

Throwaway database kalbt_portfolio_run_test (db/schema.sql, then db/portfolio.sql) filled with
the fixture of tests/recon/check_check_run.py and engine runs made by match_intents. A second
throwaway database kalbt_portfolio_run_test_bare holds only db/schema.sql. Both are dropped at the
end, even on failure. The live database is only read: SELECT row counts and to_regclass.

Run from the repo root: PYTHONPATH=. uv run python tests/portfolio/check_check_run.py
"""
import subprocess
import traceback
from datetime import date
from decimal import Decimal
from pathlib import Path

from db.connect import connect
from engine.match_intents import match_intents
from portfolio.check_run import check_run
from tests.recon.check_check_run import (
    M1,
    M2,
    M3,
    at,
    clean_run,
    intent,
    load_fixture,
    tamper,
)

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
TEST_DB = "kalbt_portfolio_run_test"
BARE_DB = "kalbt_portfolio_run_test_bare"
TABLES = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]
D = Decimal
M4 = "T-M4"   # extra market: settles three days after it closes

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def row_counts(conn):
    cur = conn.cursor()
    counts = {}
    for table in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


def by_name(results):
    assert [name for name, _, _ in results] == ["fees", "snapshots", "settlements"], \
        f"entry names {[name for name, _, _ in results]}"
    return {name: (ok, rows) for name, ok, rows in results}


def expect_all_pass(conn, run_id):
    results = by_name(check_run(conn, run_id))
    for name, (ok, rows) in results.items():
        assert ok and rows == [], f"{name} should pass, rows {rows}"


def expect_only_fail(conn, run_id, failing, row_count=1):
    """Exactly one entry fails, with row_count rows; returns its rows."""
    results = by_name(check_run(conn, run_id))
    for name, (ok, rows) in results.items():
        if name == failing:
            assert not ok and len(rows) == row_count, f"{name}: ok {ok}, rows {rows}"
        else:
            assert ok and rows == [], f"{name} should pass, rows {rows}"
    return results[failing][1]


def run_cli(run_id, db=TEST_DB):
    done = subprocess.run(["uv", "run", "python", "-m", "portfolio.check_run", str(run_id),
                           "--db", db], cwd=ROOT, capture_output=True, text=True, check=False)
    return done.returncode, done.stdout.splitlines()


def plant_snapshot(conn, run_id, day, market, net, avg_cost):
    cur = conn.cursor()
    cur.execute("ALTER TABLE position_snapshot DISABLE TRIGGER USER")
    cur.execute("INSERT INTO position_snapshot (run_id, as_of_date, market_ticker, net_units, avg_cost)"
                " VALUES (%s, %s, %s, %s, %s)", (run_id, day, market, net, avg_cost))
    cur.execute("ALTER TABLE position_snapshot ENABLE TRIGGER USER")
    conn.commit()


def snapshot_days(conn, run_id, market):
    cur = conn.cursor()
    cur.execute("SELECT as_of_date FROM position_snapshot WHERE run_id = %s AND market_ticker = %s"
                " ORDER BY 1", (run_id, market))
    return [row[0] for row in cur.fetchall()]


def add_late_market(conn):
    """M4: closes 06-02 06:00 like the others, settles yes on 06-05 12:00."""
    cur = conn.cursor()
    cur.execute("INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike,"
                " open_time, close_time, result, settled_at)"
                " VALUES (%s, 'EV-0601', 'between', 78, 82, %s, %s, 'yes', %s)",
                (M4, at(5, 31, 15, 0), at(6, 2, 6, 0), at(6, 5, 12, 0)))
    cur.executemany("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    [(M4, at(6, 1, 12, 0), D("0.40"), D("0.45"), D(100)),
                     (M4, at(6, 1, 12, 1), D("0.40"), D("0.45"), D(0))])
    conn.commit()


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

def test_01_clean_run(conn, ctx):
    run_id = clean_run(conn)
    ctx["clean"] = run_id
    results = check_run(conn, run_id)
    assert len(results) == 3, f"{len(results)} entries"
    expect_all_pass(conn, run_id)


def test_02_held_unsettled(conn, ctx):
    run_id = match_intents(conn, "held", {}, [intent(at(6, 1, 12, 0), M3, "yes", "buy", 3)])
    assert snapshot_days(conn, run_id, M3) == [date(2026, 6, 1), date(2026, 6, 2)], \
        f"engine snapshot days {snapshot_days(conn, run_id, M3)}"
    expect_all_pass(conn, run_id)
    cur = conn.cursor()
    cur.execute("SELECT day FROM portfolio_calendar WHERE run_id = %s ORDER BY 1", (run_id,))
    days = [row[0] for row in cur.fetchall()]
    assert days == [date(2026, 6, 1), date(2026, 6, 2)], f"calendar days {days}"


def test_03_snapshot_net_changed(conn, ctx):
    run_id = clean_run(conn)
    ctx["tampered"] = run_id
    tamper(conn, "position_snapshot",
           "UPDATE position_snapshot SET net_units = 9 WHERE run_id = %s AND market_ticker = %s"
           " AND as_of_date = '2026-06-01'", (run_id, M1))
    rows = expect_only_fail(conn, run_id, "snapshots")
    row = rows[0]
    assert (row[1], row[2], row[3], row[4]) == (date(2026, 6, 1), M1, D(10), D(9)), f"row {row}"


def test_04_snapshot_avg_cost_changed(conn, ctx):
    run_id = clean_run(conn)
    tamper(conn, "position_snapshot",
           "UPDATE position_snapshot SET avg_cost = avg_cost + 0.01 WHERE run_id = %s"
           " AND market_ticker = %s AND as_of_date = '2026-06-01'", (run_id, M2))
    rows = expect_only_fail(conn, run_id, "snapshots")
    assert rows[0][2] == M2 and rows[0][3] == rows[0][4], f"row {rows[0]}"


def test_05_snapshot_deleted(conn, ctx):
    run_id = clean_run(conn)
    tamper(conn, "position_snapshot",
           "DELETE FROM position_snapshot WHERE run_id = %s AND market_ticker = %s"
           " AND as_of_date = '2026-06-02'", (run_id, M3))
    rows = expect_only_fail(conn, run_id, "snapshots")
    row = rows[0]
    assert (row[1], row[2], row[3], row[4]) == (date(2026, 6, 2), M3, D(3), None), f"row {row}"


def test_06_fee_changed(conn, ctx):
    run_id = clean_run(conn)
    cur = conn.cursor()
    cur.execute("SELECT o.outcome_id FROM order_outcome o JOIN order_intent i USING (intent_id)"
                " WHERE i.run_id = %s AND i.market_ticker = %s AND o.outcome = 'filled'"
                " ORDER BY o.occurred_at LIMIT 1", (run_id, M1))
    outcome_id = cur.fetchone()[0]
    tamper(conn, "order_outcome", "UPDATE order_outcome SET fee = fee + 0.01 WHERE outcome_id = %s",
           (outcome_id,))
    rows = expect_only_fail(conn, run_id, "fees")
    row = rows[0]
    assert row[1] == outcome_id and row[2] == M1, f"row {row}"
    assert row[5] == D("0.10") and row[6] == D("0.09"), f"fee {row[5]} expected_fee {row[6]}"


def test_07_settlement_net_changed(conn, ctx):
    run_id = clean_run(conn)
    tamper(conn, "settlement",
           "UPDATE settlement SET net_units = 9 WHERE run_id = %s AND market_ticker = %s",
           (run_id, M1))
    results = by_name(check_run(conn, run_id))
    assert results["fees"] == (True, []), f"fees {results['fees']}"
    ok, rows = results["settlements"]
    assert not ok and len(rows) == 1, f"settlements: ok {ok}, rows {rows}"
    row = rows[0]
    assert row[1] == M1 and row[3] == D(9) and row[4] == D(1), f"row {row}"
    # side effect, not named by the spec: the portfolio now thinks 1 unit of M1 is still held on
    # 06-02, so the snapshot check shows that one day (portfolio side only)
    snap_rows = results["snapshots"][1]
    assert [(r[1], r[2], r[3], r[4]) for r in snap_rows] == [(date(2026, 6, 2), M1, D(1), None)], \
        f"snapshots {snap_rows}"


def test_08_settlement_payout_changed(conn, ctx):
    run_id = clean_run(conn)
    tamper(conn, "settlement",
           "UPDATE settlement SET payout = 9 WHERE run_id = %s AND market_ticker = %s",
           (run_id, M1))
    rows = expect_only_fail(conn, run_id, "settlements")
    row = rows[0]
    assert row[1] == M1 and row[5] == D(9) and row[6] == D(10), f"row {row}"


def test_09_errors(conn, ctx, bare_conn):
    try:
        check_run(conn, 999999)
    except ValueError as error:
        assert str(error) == "unknown run_id 999999", f"message {error}"
    else:
        raise AssertionError("no ValueError for an unknown run_id")
    # a run must exist for the portfolio check to be reached, so give the bare database one
    cur = bare_conn.cursor()
    cur.execute("INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end)"
                " VALUES ('bare', '{}', 'v', 1000, '2026-06-01', '2026-06-01') RETURNING run_id")
    bare_run = cur.fetchone()[0]
    bare_conn.commit()
    try:
        check_run(bare_conn, bare_run)
    except RuntimeError as error:
        assert str(error) == f"db/portfolio.sql is not applied to database {BARE_DB}", f"message {error}"
    else:
        raise AssertionError("no RuntimeError on a database without db/portfolio.sql")


def test_10_command_line(conn, ctx):
    code, lines = run_cli(ctx["clean"])
    assert code == 0 and lines[-1] == "PASS", f"clean: exit {code}, output {lines}"
    code, lines = run_cli(ctx["tampered"])
    assert code == 1 and lines[-1] == "FAIL", f"tampered: exit {code}, output {lines}"


def test_11_live_untouched(live_before, ctx):
    live = connect()
    try:
        after = row_counts(live)
        cur = live.cursor()
        cur.execute("SELECT to_regclass('portfolio_snapshot_check')")
        view = cur.fetchone()[0]
    finally:
        live.close()
    assert after == live_before, f"live row counts: before {live_before}, after {after}"
    assert view is None, f"portfolio_snapshot_check exists in the live database: {view}"


# ---------------------------------------------------------------------------
# Extra attacks
# ---------------------------------------------------------------------------

def test_x01_no_position_to_settlement(conn, ctx):
    # NO on M2 (settled no): wins 5. NO on M1 (settled yes): loses. Snapshot avg_cost is the NO price.
    run_id = match_intents(conn, "no", {}, [
        intent(at(6, 1, 2, 0), M2, "no", "buy", 5),
        intent(at(6, 1, 10, 0), M1, "no", "buy", 5)])
    cur = conn.cursor()
    cur.execute("SELECT market_ticker, net_units, avg_cost FROM position_snapshot WHERE run_id = %s"
                " ORDER BY 1, as_of_date", (run_id,))
    snaps = cur.fetchall()
    assert snaps and all(net < 0 and 0 < avg < 1 for _, net, avg in snaps), f"snapshots {snaps}"
    cur.execute("SELECT market_ticker, payout FROM settlement WHERE run_id = %s ORDER BY 1", (run_id,))
    payouts = dict(cur.fetchall())
    assert payouts == {M1: D(0), M2: D(5)}, f"payouts {payouts}"
    expect_all_pass(conn, run_id)


def test_x02_closed_to_flat(conn, ctx):
    run_id = match_intents(conn, "flat", {}, [
        intent(at(6, 1, 10, 0), M1, "yes", "buy", 5),
        intent(at(6, 1, 10, 10), M1, "yes", "sell", 5)])
    cur = conn.cursor()
    cur.execute("SELECT count(*) FROM settlement WHERE run_id = %s", (run_id,))
    n_settlements = cur.fetchone()[0]
    assert snapshot_days(conn, run_id, M1) == [], f"snapshots {snapshot_days(conn, run_id, M1)}"
    assert n_settlements == 0, f"{n_settlements} settlement rows for a flat position"
    expect_all_pass(conn, run_id)


def test_x03_two_runs_sharing_markets(conn, ctx):
    run_a = clean_run(conn)
    run_b = clean_run(conn)
    expect_all_pass(conn, run_a)
    expect_all_pass(conn, run_b)
    tamper(conn, "position_snapshot",
           "UPDATE position_snapshot SET net_units = 9 WHERE run_id = %s AND market_ticker = %s"
           " AND as_of_date = '2026-06-01'", (run_a, M1))
    expect_only_fail(conn, run_a, "snapshots")
    expect_all_pass(conn, run_b)
    tamper(conn, "settlement", "UPDATE settlement SET payout = 9 WHERE run_id = %s AND market_ticker = %s",
           (run_b, M1))
    expect_only_fail(conn, run_b, "settlements")
    # run A still has only its own snapshot fault, no settlement fault
    results = by_name(check_run(conn, run_a))
    assert results["settlements"][0] and len(results["snapshots"][1]) == 1, f"run A {results}"


def test_x04_settles_later_than_next_day(conn, ctx):
    run_id = match_intents(conn, "late", {}, [intent(at(6, 1, 12, 0), M4, "yes", "buy", 4)])
    days = snapshot_days(conn, run_id, M4)
    assert days == [date(2026, 6, d) for d in (1, 2, 3, 4)], f"engine snapshot days {days}"
    expect_all_pass(conn, run_id)
    cur = conn.cursor()
    cur.execute("SELECT max(day) FROM portfolio_calendar WHERE run_id = %s", (run_id,))
    assert cur.fetchone()[0] == date(2026, 6, 5), "calendar should run to the settlement day"


def test_x05_planted_snapshot_without_position(conn, ctx):
    run_id = clean_run(conn)
    plant_snapshot(conn, run_id, date(2026, 6, 3), M1, 1, D("0.45"))
    rows = expect_only_fail(conn, run_id, "snapshots")
    row = rows[0]
    assert (row[1], row[2], row[3], row[4]) == (date(2026, 6, 3), M1, None, D(1)), f"row {row}"


def test_x06_run_without_fills(conn, ctx):
    run_id = match_intents(conn, "nofill", {}, [intent(at(6, 1, 12, 0), M3, "yes", "sell", 2)])
    expect_all_pass(conn, run_id)
    code, lines = run_cli(run_id)
    assert code == 0 and lines == ["[PASS] fees", "[PASS] snapshots", "[PASS] settlements", "PASS"], \
        f"exit {code}, output {lines}"


def test_x07_run_without_intents(conn, ctx):
    # the engine refuses an empty intent list, so insert the bare run row directly
    cur = conn.cursor()
    cur.execute("INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end)"
                " VALUES ('empty', '{\"fee_rate\": \"0.07\"}', 'v', 1000, '2026-06-01', '2026-06-02')"
                " RETURNING run_id")
    run_id = cur.fetchone()[0]
    conn.commit()
    expect_all_pass(conn, run_id)


def test_x08_output_format(conn, ctx):
    run_id = clean_run(conn)
    for day in range(3, 15):    # 12 planted snapshots on days without a position
        plant_snapshot(conn, run_id, date(2026, 6, day), M1, 1, D("0.45"))
    code, lines = run_cli(run_id)
    assert code == 1, f"exit {code}"
    assert lines[0] == "[PASS] fees", f"lines {lines}"
    assert lines[1] == "[FAIL] snapshots (12 rows)", f"lines {lines}"
    shown = lines[2:-2]
    assert len(shown) == 10, f"{len(shown)} rows shown, expected 10: {lines}"
    assert all(line.startswith("  ") and not line.startswith("   ") for line in shown), f"{shown}"
    assert lines[-2] == "[PASS] settlements" and lines[-1] == "FAIL", f"lines {lines}"
    # rows are ordered by run_id, day, market
    assert "datetime.date(2026, 6, 3)" in shown[0], f"first row {shown[0]}"
    # fewer than 10 rows: all shown
    run_two = clean_run(conn)
    plant_snapshot(conn, run_two, date(2026, 6, 3), M1, 1, D("0.45"))
    code, lines = run_cli(run_two)
    assert lines[1] == "[FAIL] snapshots (1 rows)" and len(lines) == 5 and code == 1, f"{lines}"


def test_x09_exit_code_2(conn, ctx):
    code, lines = run_cli(999999)
    assert code == 2 and lines == ["unknown run_id 999999"], f"exit {code}, output {lines}"
    code, lines = run_cli(1, db=BARE_DB)
    assert code == 2 and lines, f"bare db: exit {code}, output {lines}"


def test_x10_check_run_never_commits(conn, ctx):
    # a read-only connection would raise on any write
    ro = connect(dbname=TEST_DB)
    try:
        ro.read_only = True
        expect_all_pass(ro, ctx["clean"])
    finally:
        ro.close()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_test(name, function, *args):
    conn = args[0]
    try:
        function(*args)
    except Exception as error:  # noqa: BLE001  one failing test must not stop the rest
        traceback.print_exc()
        report(name, False, f"{type(error).__name__}: {error}")
        conn.rollback()
    else:
        report(name, True)


def main():
    live_conn = connect()
    live_before = row_counts(live_conn)   # SELECT count(*) only
    live_conn.close()
    admin = connect()
    admin.autocommit = True
    for name in (TEST_DB, BARE_DB):
        admin.execute(f"DROP DATABASE IF EXISTS {name}")
        admin.execute(f"CREATE DATABASE {name}")
    conn = bare_conn = None
    try:
        conn = connect(dbname=TEST_DB)
        conn.execute(SCHEMA.read_text())
        conn.execute(PORTFOLIO.read_text())
        conn.commit()
        load_fixture(conn)
        add_late_market(conn)
        bare_conn = connect(dbname=BARE_DB)
        bare_conn.execute(SCHEMA.read_text())
        bare_conn.commit()

        ctx = {}
        run_test("01 clean run: three entries, all ok, no rows", test_01_clean_run, conn, ctx)
        run_test("02 held unsettled position passes, calendar 06-01 and 06-02",
                 test_02_held_unsettled, conn, ctx)
        run_test("03 snapshot net_units changed", test_03_snapshot_net_changed, conn, ctx)
        run_test("04 snapshot avg_cost changed by 0.01", test_04_snapshot_avg_cost_changed, conn, ctx)
        run_test("05 snapshot row deleted", test_05_snapshot_deleted, conn, ctx)
        run_test("06 fee changed by one cent", test_06_fee_changed, conn, ctx)
        run_test("07 settlement net_units changed", test_07_settlement_net_changed, conn, ctx)
        run_test("08 settlement payout changed", test_08_settlement_payout_changed, conn, ctx)
        run_test("09 unknown run raises ValueError, bare database raises RuntimeError",
                 test_09_errors, conn, ctx, bare_conn)
        if "clean" in ctx and "tampered" in ctx:
            run_test("10 command line exit codes and last line", test_10_command_line, conn, ctx)
        else:
            report("10 command line exit codes and last line", False, "test 1 or 3 did not run")
        run_test("11 live database row counts unchanged, no portfolio_snapshot_check view",
                 lambda c, x: test_11_live_untouched(live_before, x), conn, ctx)

        run_test("EXTRA NO position held to settlement", test_x01_no_position_to_settlement, conn, ctx)
        run_test("EXTRA position closed to flat: no snapshot, no settlement", test_x02_closed_to_flat,
                 conn, ctx)
        run_test("EXTRA two runs sharing markets do not cross-talk", test_x03_two_runs_sharing_markets,
                 conn, ctx)
        run_test("EXTRA market settling three days after close keeps snapshotting",
                 test_x04_settles_later_than_next_day, conn, ctx)
        run_test("EXTRA snapshot planted on a day without a position",
                 test_x05_planted_snapshot_without_position, conn, ctx)
        run_test("EXTRA run with no fills passes everywhere", test_x06_run_without_fills, conn, ctx)
        run_test("EXTRA run with no intents passes everywhere", test_x07_run_without_intents, conn, ctx)
        run_test("EXTRA output format: 10 rows shown of 12, indented, last line FAIL",
                 test_x08_output_format, conn, ctx)
        run_test("EXTRA exit code 2 on unknown run and on a bare database", test_x09_exit_code_2,
                 conn, ctx)
        if "clean" in ctx:
            run_test("EXTRA check_run works on a read-only connection", test_x10_check_run_never_commits,
                     conn, ctx)
    finally:
        for c in (conn, bare_conn):
            if c is not None:
                c.close()
        for name in (TEST_DB, BARE_DB):
            admin.execute(f"DROP DATABASE IF EXISTS {name}")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
