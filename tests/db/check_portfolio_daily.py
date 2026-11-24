"""Checks the day-by-day views of db/portfolio.sql in a throwaway database (spec C02).

Tests 1 to 11 are the agreed ones; checks labelled EXTRA are attacks added by the tester.

Run: PYTHONPATH=. uv run python tests/db/check_portfolio_daily.py
"""
import os
import subprocess
import sys
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from db.connect import read_password

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_SQL = (ROOT / "db" / "schema.sql").read_text()
PORTFOLIO_SQL = (ROOT / "db" / "portfolio.sql").read_text()
TEST_DB = "kalbt_portfolio_daily_test"
FEE_PARAMS = {"fee_rate": "0.07"}

DAILY_COLS = ("day, premium_paid, premium_received, fees, payouts, cash_end, cost_basis_open, "
              "realized_pnl_cum, fees_cum, net_pnl_cum, open_markets")
SHARED_COLS = ["cost_basis_open", "realized_pnl_cum", "fees_cum", "net_pnl_cum", "open_markets"]

failures = []
all_runs = []   # every run created, for the checks that look at all of them


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def ts(day, hour=0, minute=0, second=0, micro=0):
    """UTC timestamp in June 2026."""
    return datetime(2026, 6, day, hour, minute, second, micro, tzinfo=UTC)


def admin():
    return psycopg.connect(host="127.0.0.1", port=5433, user="kalbt", dbname="kalbt",
                           password=read_password(), autocommit=True)


def test_connection():
    return psycopg.connect(host="127.0.0.1", port=5433, user="kalbt", dbname=TEST_DB,
                           password=read_password(), autocommit=True)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def add_event(cur, event, series, underlying, event_day):
    cur.execute(
        "INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source) "
        "VALUES (%s, %s, %s, %s, 'NWS')", (event, series, underlying, date(2026, 6, event_day)))


def add_market(cur, ticker, event):
    cur.execute(
        "INSERT INTO market (ticker, event_ticker, open_time, close_time) VALUES (%s, %s, %s, %s)",
        (ticker, event, datetime(2026, 5, 31, 15, tzinfo=UTC), ts(30, 6)))


def add_run(cur, start_day=1, end_day=4, cash=1000):
    cur.execute(
        "INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end) "
        "VALUES ('t', %s, 'v', %s, %s, %s) RETURNING run_id",
        (Jsonb(FEE_PARAMS), cash, date(2026, 6, start_day), date(2026, 6, end_day)))
    run_id = cur.fetchone()[0]
    all_runs.append(run_id)
    return run_id


def add_intent(cur, run_id, market, side, action, qty, when):
    cur.execute(
        "INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING intent_id",
        (run_id, when, market, side, action, qty))
    return cur.fetchone()[0]


def fill(cur, run_id, market, side, action, qty, price, fee, when):
    intent_id = add_intent(cur, run_id, market, side, action, qty, when)
    cur.execute(
        "INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee) "
        "VALUES (%s, %s, 'filled', %s, %s, %s)", (intent_id, when, qty, price, fee))


def refuse(cur, run_id, market, outcome, when):
    intent_id = add_intent(cur, run_id, market, "yes", "buy", 1, when)
    cur.execute(
        "INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, reason_code) "
        "VALUES (%s, %s, %s, 0, 'why')", (intent_id, when, outcome))


def settle(cur, run_id, market, when, result, net_units, payout):
    cur.execute(
        "INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout) "
        "VALUES (%s, %s, %s, %s, %s, %s)", (run_id, market, when, result, net_units, payout))


# ---------------------------------------------------------------------------
# Readers and expected-value helpers
# ---------------------------------------------------------------------------

def dec(values):
    return [Decimal(str(x)) for x in values]


def want_daily(table):
    """Rows (day number, then the ten numbers of DAILY_COLS after day)."""
    return [(date(2026, 6, row[0]), *dec(row[1:])) for row in table]


def want_keyed(table):
    """Rows (day number, key text, numbers...)."""
    return [(date(2026, 6, row[0]), row[1], *dec(row[2:])) for row in table]


def read_daily(cur, run_id):
    cur.execute(f"SELECT {DAILY_COLS} FROM portfolio_daily WHERE run_id = %s ORDER BY day",
                (run_id,))
    return cur.fetchall()


def read_positions(cur, run_id):
    cur.execute("SELECT day, market_ticker, net_units, cost_basis, avg_cost "
                "FROM portfolio_position_daily WHERE run_id = %s ORDER BY day, market_ticker",
                (run_id,))
    return cur.fetchall()


def read_markets(cur, run_id):
    cur.execute("SELECT day, market_ticker, net_units, cost_basis, realized_cum, fees_cum, cash_cum "
                "FROM portfolio_market_daily WHERE run_id = %s ORDER BY day, market_ticker",
                (run_id,))
    return cur.fetchall()


def read_underlying(cur, run_id):
    cur.execute(f"SELECT day, underlying_id, {', '.join(SHARED_COLS)} "
                "FROM portfolio_daily_underlying WHERE run_id = %s ORDER BY day, underlying_id",
                (run_id,))
    return cur.fetchall()


def read_days(cur, run_id):
    cur.execute("SELECT day FROM portfolio_calendar WHERE run_id = %s ORDER BY day", (run_id,))
    return [row[0] for row in cur.fetchall()]


def check_daily(cur, name, run_id, table):
    actual = read_daily(cur, run_id)
    want = want_daily(table)
    report(name, actual == want, f"actual {actual} expected {want}")


def check_positions(cur, name, run_id, table):
    actual = read_positions(cur, run_id)
    want = want_keyed(table)
    report(name, actual == want, f"actual {actual} expected {want}")


def ident_breaks(cur, run_id=None):
    """Rows of portfolio_daily where cash_end + cost_basis_open != starting_cash + net_pnl_cum."""
    sql = ("SELECT run_id, day, cash_end + cost_basis_open, starting_cash + net_pnl_cum "
           "FROM portfolio_daily WHERE cash_end + cost_basis_open <> starting_cash + net_pnl_cum")
    if run_id is None:
        cur.execute(sql)
    else:
        cur.execute(sql + " AND run_id = %s", (run_id,))
    return cur.fetchall()


def underlying_breaks(cur):
    """Days where the per-underlying sums differ from portfolio_daily (or are missing)."""
    sums = ", ".join(f"sum({c}) AS {c}" for c in SHARED_COLS)
    differs = " OR ".join(f"d.{c} IS DISTINCT FROM u.{c}" for c in SHARED_COLS)
    cur.execute(
        f"SELECT d.run_id, d.day FROM portfolio_daily d LEFT JOIN ("
        f"  SELECT run_id, day, {sums} FROM portfolio_daily_underlying GROUP BY run_id, day) u "
        f"ON u.run_id = d.run_id AND u.day = d.day WHERE {differs}")
    return cur.fetchall()


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

RUN1_DAILY = [
    (1, "5.50", 0,      "0.25", 0, "994.25",  "5.50", 0,      "0.25", "-0.25", 2),
    (2, 0,      "2.20", "0.07", 0, "996.38",  "3.90", "0.60", "0.32", "0.28",  2),
    (3, 0,      0,      0,      0, "996.38",  "3.90", "0.60", "0.32", "0.28",  2),
    (4, 0,      0,      0,      6, "1002.38", 0,      "2.70", "0.32", "2.38",  0),
]

RUN1_POSITIONS = [
    (1, "A", 10, "4.00", "0.4000"), (1, "B", -5, "1.50", "0.3000"),
    (2, "A", 6, "2.40", "0.4000"), (2, "B", -5, "1.50", "0.3000"),
    (3, "A", 6, "2.40", "0.4000"), (3, "B", -5, "1.50", "0.3000"),
]


def setup_base(cur):
    cur.execute("INSERT INTO underlying (underlying_id, description) VALUES "
                "('NYC', 'New York'), ('CHI', 'Chicago')")
    cur.execute("INSERT INTO series (ticker, underlying_id) VALUES "
                "('KXHIGHNY', 'NYC'), ('KXHIGHCHI', 'CHI')")
    add_event(cur, "EV1", "KXHIGHNY", "NYC", 3)
    add_market(cur, "A", "EV1")
    add_market(cur, "B", "EV1")

    run1 = add_run(cur)
    fill(cur, run1, "A", "yes", "buy", 10, "0.40", "0.17", ts(1, 14, 1))
    fill(cur, run1, "B", "no", "buy", 5, "0.30", "0.08", ts(1, 23, 59, 59))
    fill(cur, run1, "A", "yes", "sell", 4, "0.55", "0.07", ts(2, 0, 0, 0))
    settle(cur, run1, "A", ts(4, 13), "yes", 6, 6)
    settle(cur, run1, "B", ts(4, 13), "yes", -5, 0)
    run2 = add_run(cur, 1, 2, 500)
    return run1, run2


def check_test1(cur, run1):
    check_daily(cur, "1: portfolio_daily run 1 has the four expected rows and no other days",
                run1, RUN1_DAILY)
    cur.execute("SELECT DISTINCT starting_cash FROM portfolio_daily WHERE run_id = %s", (run1,))
    report("1b: starting_cash is 1000 on every row", cur.fetchall() == [(Decimal(1000),)])


def check_test2(cur, run1):
    daily = {row[0]: row for row in read_daily(cur, run1)}
    # columns: day, paid, received, fees, ...
    report("2a: the 23:59:59 fill of B counts in 06-01 (paid 5.50, fees 0.25)",
           daily[date(2026, 6, 1)][1] == Decimal("5.50") and daily[date(2026, 6, 1)][3] == Decimal("0.25"),
           f"actual {daily[date(2026, 6, 1)]}")
    report("2b: the 00:00:00 fill of A counts in 06-02 (received 2.20, fees 0.07)",
           daily[date(2026, 6, 2)][2] == Decimal("2.20") and daily[date(2026, 6, 2)][3] == Decimal("0.07"),
           f"actual {daily[date(2026, 6, 2)]}")


def check_test3(cur, run1):
    broken = ident_breaks(cur, run1)
    report("3: cash_end + cost_basis_open = starting_cash + net_pnl_cum on every row of run 1",
           broken == [], f"{broken}")


def check_test4(cur, run1):
    check_positions(cur, "4: portfolio_position_daily run 1 (no row on 06-04)", run1,
                    RUN1_POSITIONS)


def check_test5(cur, run1):
    actual = read_underlying(cur, run1)
    want = []
    for row in read_daily(cur, run1):
        day, open_markets = row[0], row[10]
        want.append((day, "CHI", *dec([0, 0, 0, 0, 0])))
        want.append((day, "NYC", row[6], row[7], row[8], row[9], Decimal(open_markets)))
    want.sort(key=lambda r: (r[0], r[1]))
    report("5: portfolio_daily_underlying run 1: NYC equals portfolio_daily, CHI is all 0 on "
           "each day", actual == want and len(actual) == 8, f"actual {actual} expected {want}")


def check_test6(cur, run2):
    check_daily(cur, "6a: run 2 has two portfolio_daily rows, cash_end 500, all else 0", run2, [
        (1, 0, 0, 0, 0, 500, 0, 0, 0, 0, 0),
        (2, 0, 0, 0, 0, 500, 0, 0, 0, 0, 0)])
    actual = read_underlying(cur, run2)
    want = [(date(2026, 6, d), u, *dec([0] * 5)) for d in (1, 2) for u in ("CHI", "NYC")]
    report("6b: run 2 has four portfolio_daily_underlying rows, all 0", actual == want,
           f"actual {actual}")


def check_test7(cur, run3):
    days = read_days(cur, run3)
    report("7a: run dated 06-02 to 06-02 with events 06-01 and 06-04 has days 06-01 to 06-04",
           days == [date(2026, 6, d) for d in (1, 2, 3, 4)], f"actual {days}")
    check_daily(cur, "7b: portfolio_daily of the extended run (hand values)", run3, [
        (1, "1.00", 0, "0.03", 0, "998.97",  "1.00", 0,      "0.03", "-0.03", 1),
        (2, 0,      0, 0,      0, "998.97",  "1.00", 0,      "0.03", "-0.03", 1),
        (3, 0,      0, 0,      0, "998.97",  "1.00", 0,      "0.03", "-0.03", 1),
        (4, 0,      0, 0,      2, "1000.97", 0,      "1.00", "0.03", "0.97",  0)])


def check_test9(cur, run4):
    actual = read_underlying(cur, run4)
    # both positions stay open and their markets close on 06-30: days 06-04 to 06-30 repeat 06-03
    want_rows = [
        (1, "CHI", 0, 0, 0, 0, 0),         (1, "NYC", "2.00", 0, "0.05", "-0.05", 1),
        (2, "CHI", "0.50", 0, "0.02", "-0.02", 1), (2, "NYC", "2.00", 0, "0.05", "-0.05", 1),
        (3, "CHI", "0.50", 0, "0.02", "-0.02", 1), (3, "NYC", "2.00", 0, "0.05", "-0.05", 1)]
    for day in range(4, 31):
        want_rows.append((day, "CHI", "0.50", 0, "0.02", "-0.02", 1))
        want_rows.append((day, "NYC", "2.00", 0, "0.05", "-0.05", 1))
    want = want_keyed(want_rows)
    report("9a: each underlying carries only its own numbers, 06-01 to 06-30", actual == want,
           f"actual {actual} expected {want}")
    daily_rows = [
        (1, "2.00", 0, "0.05", 0, "997.95", "2.00", 0, "0.05", "-0.05", 1),
        (2, "0.50", 0, "0.02", 0, "997.43", "2.50", 0, "0.07", "-0.07", 2),
        (3, 0,      0, 0,      0, "997.43", "2.50", 0, "0.07", "-0.07", 2)]
    for day in range(4, 31):
        daily_rows.append((day, 0, 0, 0, 0, "997.43", "2.50", 0, "0.07", "-0.07", 2))
    check_daily(cur, "9b: portfolio_daily of the two-underlying run, 06-01 to 06-30", run4,
                daily_rows)


def check_test8(cur, label):
    broken = underlying_breaks(cur)
    cur.execute("SELECT count(*) FROM portfolio_daily")
    report(f"{label}: per-underlying sums equal the portfolio on every run and day "
           f"({cur.fetchone()[0]} days)", broken == [], f"{broken}")


def live_state():
    with admin() as conn:
        cur = conn.cursor()
        counts = []
        for table in ("run", "order_intent", "order_outcome", "settlement"):
            cur.execute(f"SELECT count(*) FROM {table}")
            counts.append(cur.fetchone()[0])
        cur.execute("SELECT count(*) FROM pg_views WHERE viewname = 'portfolio_daily'")
        return counts, cur.fetchone()[0]


def check_test10_c01():
    result = subprocess.run([sys.executable, str(ROOT / "tests" / "db" / "check_portfolio.py")],
                            check=False, cwd=ROOT, capture_output=True, text=True,
                            env={**os.environ, "PYTHONPATH": str(ROOT)})
    lines = result.stdout.splitlines()
    fails = [line for line in lines if line.startswith("[FAIL]")]
    passes = [line for line in lines if line.startswith("[PASS]")]
    report("10b: tests/db/check_portfolio.py (C01) still passes unchanged",
           result.returncode == 0 and not fails and len(passes) > 0,
           f"exit {result.returncode}, {len(passes)} pass, fails {fails}, stderr {result.stderr[-300:]}")


# ---------------------------------------------------------------------------
# EXTRA checks
# ---------------------------------------------------------------------------

def setup_extra_markets(cur):
    """EV3 on NYC (second NYC event, markets E and F), EV2 on CHI (market D)."""
    add_event(cur, "EV3", "KXHIGHNY", "NYC", 10)
    add_market(cur, "E", "EV3")
    add_market(cur, "F", "EV3")
    add_event(cur, "EV2", "KXHIGHCHI", "CHI", 3)
    add_market(cur, "D", "EV2")


def extra_boundaries(cur):
    run = add_run(cur, 10, 12)
    fill(cur, run, "E", "yes", "buy", 2, "0.50", "0.01", ts(10, 0, 0, 0, 0))
    fill(cur, run, "E", "yes", "buy", 2, "0.50", "0.01", ts(10, 23, 59, 59, 999999))
    fill(cur, run, "E", "yes", "buy", 1, "0.40", "0.01", ts(11, 0, 0, 0, 0))
    check_daily(cur, "EXTRA boundaries: events at 00:00:00 and 23:59:59.999999 land on their "
                "own UTC day", run, [
        (10, "2.00", 0, "0.02", 0, "997.98", "2.00", 0, "0.02", "-0.02", 1),
        (11, "0.40", 0, "0.01", 0, "997.57", "2.40", 0, "0.03", "-0.03", 1),
        *[(d, 0, 0, 0, 0, "997.57", "2.40", 0, "0.03", "-0.03", 1) for d in range(12, 31)]])
    actual = [(r[0], r[2]) for r in read_markets(cur, run)]
    want = [(date(2026, 6, 10), 4)] + [(date(2026, 6, d), 5) for d in range(11, 31)]
    report("EXTRA boundaries: market net_units 4, then 5 every day to 06-30",
           actual == want, f"actual {actual}")


def extra_all_before_start(cur):
    run = add_run(cur, 15, 16)
    fill(cur, run, "E", "yes", "buy", 1, "0.30", "0.01", ts(12, 10))
    settle(cur, run, "E", ts(13, 12), "no", 1, 0)
    days = read_days(cur, run)
    report("EXTRA events all before date_start: calendar runs 06-12 to 06-16",
           days == [date(2026, 6, d) for d in range(12, 17)], f"actual {days}")
    check_daily(cur, "EXTRA events all before date_start: daily values", run, [
        (12, "0.30", 0, "0.01", 0, "999.69", "0.30", 0,       "0.01", "-0.01", 1),
        (13, 0,      0, 0,      0, "999.69", 0,      "-0.30", "0.01", "-0.31", 0),
        (14, 0,      0, 0,      0, "999.69", 0,      "-0.30", "0.01", "-0.31", 0),
        (15, 0,      0, 0,      0, "999.69", 0,      "-0.30", "0.01", "-0.31", 0),
        (16, 0,      0, 0,      0, "999.69", 0,      "-0.30", "0.01", "-0.31", 0)])


def extra_reopen(cur):
    run = add_run(cur, 10, 16)
    fill(cur, run, "F", "yes", "buy", 4, "0.50", "0.05", ts(10, 10))
    fill(cur, run, "F", "yes", "sell", 4, "0.70", "0.06", ts(11, 10))
    fill(cur, run, "F", "yes", "buy", 2, "0.60", "0.03", ts(14, 10))
    settle(cur, run, "F", ts(16, 12), "yes", 2, 2)   # a later day than the last fill
    check_daily(cur, "EXTRA closed to flat then reopened, settled on a later day: portfolio_daily",
                run, [
        (10, "2.00", 0,      "0.05", 0, "997.95",  "2.00", 0,      "0.05", "-0.05", 1),
        (11, 0,      "2.80", "0.06", 0, "1000.69", 0,      "0.80", "0.11", "0.69",  0),
        (12, 0,      0,      0,      0, "1000.69", 0,      "0.80", "0.11", "0.69",  0),
        (13, 0,      0,      0,      0, "1000.69", 0,      "0.80", "0.11", "0.69",  0),
        (14, "1.20", 0,      "0.03", 0, "999.46",  "1.20", "0.80", "0.14", "0.66",  1),
        (15, 0,      0,      0,      0, "999.46",  "1.20", "0.80", "0.14", "0.66",  1),
        (16, 0,      0,      0,      2, "1001.46", 0,      "1.60", "0.14", "1.46",  0)])
    check_positions(cur, "EXTRA flat days give no position row, reopened days do", run, [
        (10, "F", 4, "2.00", "0.5000"),
        (14, "F", 2, "1.20", "0.6000"), (15, "F", 2, "1.20", "0.6000")])
    actual = read_markets(cur, run)
    want = want_keyed([
        (10, "F", 4, "2.00", 0,      "0.05", "-2.05"),
        (11, "F", 0, 0,      "0.80", "0.11", "0.69"),
        (12, "F", 0, 0,      "0.80", "0.11", "0.69"),
        (13, "F", 0, 0,      "0.80", "0.11", "0.69"),
        (14, "F", 2, "1.20", "0.80", "0.14", "-0.54"),
        (15, "F", 2, "1.20", "0.80", "0.14", "-0.54"),
        (16, "F", 0, 0,      "1.60", "0.14", "1.46")])
    report("EXTRA flat market keeps its realized P&L, fees and cash in portfolio_market_daily",
           actual == want, f"actual {actual} expected {want}")


def extra_two_events(cur):
    run = add_run(cur, 20, 21)
    fill(cur, run, "A", "yes", "buy", 2, "0.50", "0.02", ts(20, 10))      # NYC, EV1
    fill(cur, run, "E", "no", "buy", 3, "0.40", "0.03", ts(20, 11))       # NYC, EV3
    fill(cur, run, "F", "yes", "buy", 5, "0.20", "0.04", ts(21, 10))      # NYC, EV3
    fill(cur, run, "D", "yes", "buy", 1, "0.50", "0.01", ts(21, 11))      # CHI
    check_daily(cur, "EXTRA several markets in two NYC events plus CHI: portfolio_daily", run, [
        (20, "2.20", 0, "0.05", 0, "997.75", "2.20", 0, "0.05", "-0.05", 2),
        (21, "1.50", 0, "0.05", 0, "996.20", "3.70", 0, "0.10", "-0.10", 4),
        *[(d, 0, 0, 0, 0, "996.20", "3.70", 0, "0.10", "-0.10", 4) for d in range(22, 31)]])
    actual = read_underlying(cur, run)
    want_rows = [
        (20, "CHI", 0, 0, 0, 0, 0),
        (20, "NYC", "2.20", 0, "0.05", "-0.05", 2),
        (21, "CHI", "0.50", 0, "0.01", "-0.01", 1),
        (21, "NYC", "3.20", 0, "0.09", "-0.09", 3)]
    for day in range(22, 31):
        want_rows.append((day, "CHI", "0.50", 0, "0.01", "-0.01", 1))
        want_rows.append((day, "NYC", "3.20", 0, "0.09", "-0.09", 3))
    want = want_keyed(want_rows)
    report("EXTRA two NYC events sum into one NYC row; CHI separate", actual == want,
           f"actual {actual} expected {want}")


def extra_fractions(cur):
    run = add_run(cur, 22, 23, 100)
    fill(cur, run, "A", "yes", "buy", "2.5", "0.33", "0.02", ts(22, 10))
    fill(cur, run, "A", "yes", "sell", "1.0", "0.50", "0.01", ts(23, 10))
    check_daily(cur, "EXTRA fractional quantities: portfolio_daily", run, [
        (22, "0.825", 0,      "0.02", 0, "99.155", "0.825", 0,      "0.02", "-0.02", 1),
        (23, 0,       "0.50", "0.01", 0, "99.645", "0.495", "0.17", "0.03", "0.14",  1),
        *[(d, 0, 0, 0, 0, "99.645", "0.495", "0.17", "0.03", "0.14", 1) for d in range(24, 31)]])
    check_positions(cur, "EXTRA fractional quantities: position rows", run, [
        (22, "A", "2.5", "0.825", "0.3300"),
        *[(d, "A", "1.5", "0.495", "0.3300") for d in range(23, 31)]])


def extra_avg_cost_rounding(cur):
    run = add_run(cur, 24, 24, 100)
    fill(cur, run, "A", "yes", "buy", 1, "0.40", "0.01", ts(24, 10))
    fill(cur, run, "A", "yes", "buy", 2, "0.45", "0.01", ts(24, 11))
    fill(cur, run, "B", "no", "buy", 1, "0.40", "0.01", ts(24, 12))
    fill(cur, run, "B", "no", "buy", 2, "0.45", "0.01", ts(24, 13))
    check_positions(cur, "EXTRA avg_cost 1.30 / 3 rounds to 0.4333 for a long and a short", run, [
        *[row for d in range(24, 31)
          for row in ((d, "A", 3, "1.30", "0.4333"), (d, "B", -3, "1.30", "0.4333"))]])
    check_daily(cur, "EXTRA avg_cost run: portfolio_daily", run, [
        (24, "2.60", 0, "0.04", 0, "97.36", "2.60", 0, "0.04", "-0.04", 2),
        *[(d, 0, 0, 0, 0, "97.36", "2.60", 0, "0.04", "-0.04", 2) for d in range(25, 31)]])


def extra_rejected_only(cur):
    run = add_run(cur, 25, 27, 200)
    refuse(cur, run, "A", "rejected", ts(25, 12))
    refuse(cur, run, "A", "blocked", ts(26, 12))
    zero_rows = [(d, 0, 0, 0, 0, 200, 0, 0, 0, 0, 0) for d in (25, 26, 27)]
    check_daily(cur, "EXTRA run with only rejected and blocked outcomes: calendar from run dates, "
                "cash 200, all else 0", run, zero_rows)
    underlying = read_underlying(cur, run)
    want = [(date(2026, 6, d), u, *dec([0] * 5)) for d in (25, 26, 27) for u in ("CHI", "NYC")]
    report("EXTRA rejected-only run: six underlying rows, all 0", underlying == want,
           f"actual {underlying}")
    report("EXTRA rejected-only run: no market or position rows",
           read_markets(cur, run) == [] and read_positions(cur, run) == [])


def extra_premium_split(cur):
    run = add_run(cur, 28, 28)
    fill(cur, run, "A", "yes", "buy", 10, "0.40", "0.17", ts(28, 10))
    fill(cur, run, "A", "yes", "sell", 4, "0.55", "0.07", ts(28, 11))
    fill(cur, run, "B", "no", "buy", 5, "0.30", "0.08", ts(28, 12))
    check_daily(cur, "EXTRA buy and closing sell on one day: premium_paid and premium_received "
                "split", run, [
        (28, "5.50", "2.20", "0.32", 0, "996.38", "3.90", "0.60", "0.32", "0.28", 2),
        *[(d, 0, 0, 0, 0, "996.38", "3.90", "0.60", "0.32", "0.28", 2) for d in (29, 30)]])


def extra_flip(cur):
    run = add_run(cur, 29, 29, 50)
    fill(cur, run, "A", "yes", "buy", 2, "0.50", "0.01", ts(29, 10))
    fill(cur, run, "A", "yes", "sell", 5, "0.70", "0.02", ts(29, 11))   # closes 2, opens short 3
    check_daily(cur, "EXTRA a sell that flips long to short: net cash of the flip is premium "
                "received", run, [
        (29, "1.00", "0.50", "0.03", 0, "49.47", "0.90", "0.40", "0.03", "0.37", 1),
        (30, 0, 0, 0, 0, "49.47", "0.90", "0.40", "0.03", "0.37", 1)])
    check_positions(cur, "EXTRA flip: short 3 YES costs 0.90", run, [
        (29, "A", -3, "0.90", "0.3000"), (30, "A", -3, "0.90", "0.3000")])


def extra_global(cur):
    check_test8(cur, "EXTRA 8 over all runs")
    broken = ident_breaks(cur)
    report(f"EXTRA identity cash_end + cost_basis_open = starting_cash + net_pnl_cum on every row "
           f"of all {len(all_runs)} runs", broken == [], f"{broken}")

    cur.execute(
        "SELECT run_id, day FROM ("
        "  SELECT run_id, day, cash_end, starting_cash + sum(premium_received - premium_paid - fees "
        "         + payouts) OVER (PARTITION BY run_id ORDER BY day) AS rebuilt "
        "  FROM portfolio_daily) t WHERE cash_end <> rebuilt")
    bad = cur.fetchall()
    report("EXTRA cash_end equals starting_cash plus the running sum of the day columns", bad == [],
           f"{bad}")

    cur.execute(
        "SELECT run_id FROM portfolio_calendar GROUP BY run_id "
        "HAVING count(*) <> count(DISTINCT day) OR max(day) - min(day) + 1 <> count(*)")
    bad = cur.fetchall()
    report("EXTRA calendar days are unique and contiguous for every run", bad == [], f"{bad}")

    cur.execute("SELECT count(*) FROM portfolio_calendar c LEFT JOIN portfolio_daily d "
                "USING (run_id, day) WHERE d.run_id IS NULL")
    missing = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM (SELECT run_id, day FROM portfolio_daily "
                "GROUP BY 1, 2 HAVING count(*) > 1) t")
    dup = cur.fetchone()[0]
    report("EXTRA portfolio_daily has exactly one row per calendar day", missing == 0 and dup == 0,
           f"missing {missing} duplicated {dup}")

    cur.execute("SELECT count(*) FROM portfolio_calendar c CROSS JOIN underlying u")
    expected_rows = cur.fetchone()[0]
    cur.execute("SELECT count(*) FROM portfolio_daily_underlying")
    report("EXTRA portfolio_daily_underlying has runs x days x underlyings rows",
           cur.fetchone()[0] == expected_rows)

    cur.execute("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname = 'public' AND c.relkind = 'v' AND c.relname LIKE 'portfolio_%' "
                "AND obj_description(c.oid, 'pg_class') IS NULL")
    report("EXTRA every portfolio view has a COMMENT", cur.fetchone()[0] == 0)
    cur.execute("SELECT c.relname, a.attname FROM pg_class c "
                "JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped "
                "WHERE c.relname IN ('portfolio_calendar', 'portfolio_market_daily', "
                "'portfolio_position_daily', 'portfolio_daily', 'portfolio_daily_underlying') "
                "AND col_description(c.oid, a.attnum) IS NULL")
    bad = cur.fetchall()
    report("EXTRA every column of the five new views has a COMMENT", bad == [], f"{bad}")


def main():
    before_counts, before_view = live_state()
    with admin() as conn:
        conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        conn.execute(f"CREATE DATABASE {TEST_DB}")
    try:
        with test_connection() as conn:
            cur = conn.cursor()
            cur.execute(SCHEMA_SQL)
            cur.execute(PORTFOLIO_SQL)
            run1, run2 = setup_base(cur)

            check_test1(cur, run1)
            check_test2(cur, run1)
            check_test3(cur, run1)
            check_test4(cur, run1)
            check_test5(cur, run1)
            check_test6(cur, run2)

            setup_extra_markets(cur)   # EV2 on CHI with market D, plus EV3 markets E and F

            run3 = add_run(cur, 2, 2)
            fill(cur, run3, "A", "yes", "buy", 2, "0.50", "0.03", ts(1, 10))
            settle(cur, run3, "A", ts(4, 13), "yes", 2, 2)
            check_test7(cur, run3)

            run4 = add_run(cur, 1, 3)
            fill(cur, run4, "A", "yes", "buy", 4, "0.50", "0.05", ts(1, 10))
            fill(cur, run4, "D", "yes", "buy", 2, "0.25", "0.02", ts(2, 10))
            check_test9(cur, run4)
            check_test8(cur, "8")

            cur.execute(PORTFOLIO_SQL)
            report("10a: applying db/portfolio.sql a second time succeeds", True)
            check_daily(cur, "10a2: run 1 rows unchanged after the second apply", run1, RUN1_DAILY)

            extra_boundaries(cur)
            extra_all_before_start(cur)
            extra_reopen(cur)
            extra_two_events(cur)
            extra_fractions(cur)
            extra_avg_cost_rounding(cur)
            extra_rejected_only(cur)
            extra_premium_split(cur)
            extra_flip(cur)
            extra_global(cur)
    finally:
        with admin() as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")

    check_test10_c01()

    after_counts, after_view = live_state()
    report("11a: live database journals have the same row counts", before_counts == after_counts,
           f"before {before_counts} after {after_counts}")
    report("11b: live database has no portfolio_daily view",
           before_view == after_view == 0, f"before {before_view} after {after_view}")

    print(f"{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
