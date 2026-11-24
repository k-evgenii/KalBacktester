"""Checks db/portfolio.sql in a throwaway database (spec C01, tests 1 to 12, then extra checks).

Run: PYTHONPATH=. uv run python tests/db/check_portfolio.py
"""
import os
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from db.connect import read_password

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA_SQL = (ROOT / "db" / "schema.sql").read_text()
PORTFOLIO_SQL = (ROOT / "db" / "portfolio.sql").read_text()
TEST_DB = f"kalbt_portfolio_test_{os.getpid()}"
FEE_PARAMS = {"fee_rate": "0.07"}

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def d(value):
    return None if value is None else Decimal(str(value))


def at(day, hour=0, minute=0):
    """UTC timestamp in June 2026."""
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


def admin():
    return psycopg.connect(host="127.0.0.1", port=5433, user="kalbt", dbname="kalbt",
                           password=read_password(), autocommit=True)


def test_connection():
    return psycopg.connect(host="127.0.0.1", port=5433, user="kalbt", dbname=TEST_DB,
                           password=read_password(), autocommit=True)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def add_market(cur, ticker):
    cur.execute(
        "INSERT INTO market (ticker, event_ticker, open_time, close_time) VALUES (%s, 'EV1', %s, %s)",
        (ticker, datetime(2026, 5, 31, 15, tzinfo=UTC), at(4, 6)))


def add_run(cur, params=None, cash=1000):
    cur.execute(
        "INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end) "
        "VALUES ('t', %s, 'v', %s, '2026-06-01', '2026-06-04') RETURNING run_id",
        (Jsonb(FEE_PARAMS if params is None else params), cash))
    return cur.fetchone()[0]


def add_intent(cur, run_id, market, side, action, qty, ts):
    cur.execute(
        "INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity) "
        "VALUES (%s, %s, %s, %s, %s, %s) RETURNING intent_id",
        (run_id, ts, market, side, action, qty))
    return cur.fetchone()[0]


def fill(cur, run_id, market, side, action, qty, price, fee, ts):
    """Intent plus a filled outcome. Returns the outcome_id."""
    intent_id = add_intent(cur, run_id, market, side, action, qty, ts)
    cur.execute(
        "INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee) "
        "VALUES (%s, %s, 'filled', %s, %s, %s) RETURNING outcome_id",
        (intent_id, ts, qty, price, fee))
    return cur.fetchone()[0]


def refuse(cur, run_id, market, outcome, reason_code, ts):
    intent_id = add_intent(cur, run_id, market, "yes", "buy", 1, ts)
    cur.execute(
        "INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, reason_code) "
        "VALUES (%s, %s, %s, 0, %s)", (intent_id, ts, outcome, reason_code))


def settle(cur, run_id, market, ts, result, net_units, payout):
    cur.execute(
        "INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout) "
        "VALUES (%s, %s, %s, %s, %s, %s)", (run_id, market, ts, result, net_units, payout))


def ledger(cur, run_id):
    """Ledger rows of a run in the view's window order: ts, kind, seq, market."""
    cur.execute(
        "SELECT market_ticker, kind, delta, yes_price, cash_flow, fee, net_after, "
        "cost_basis_after, realized_cum FROM portfolio_ledger WHERE run_id = %s "
        "ORDER BY ts, kind, seq, market_ticker", (run_id,))
    return cur.fetchall()


def expected_rows(table):
    """Rows given as strings or ints; market and kind stay text."""
    return [(m, k, *[Decimal(str(x)) for x in rest]) for m, k, *rest in table]


def identity_failures(cur, run_id, start_cash):
    """Test 3 identity after every event. Returns a list of text lines for each break."""
    cur.execute(
        "SELECT market_ticker, kind, cash_flow, fee, cost_basis_after, realized_cum "
        "FROM portfolio_ledger WHERE run_id = %s ORDER BY ts, kind, seq, market_ticker", (run_id,))
    cash = Decimal(0)
    fees = Decimal(0)
    latest = {}
    broken = []
    for n, (market, kind, cash_flow, fee, basis, realized) in enumerate(cur.fetchall(), start=1):
        cash += cash_flow
        fees += fee
        latest[market] = (basis, realized)
        left = start_cash + cash + sum(b for b, _ in latest.values())
        right = start_cash + sum(r for _, r in latest.values()) - fees
        if left != right:
            broken.append(f"event {n} ({market} {kind}): left {left} right {right}")
    return broken


# ---------------------------------------------------------------------------
# Checks for the spec's agreed tests
# ---------------------------------------------------------------------------

RUN1_EXPECTED = expected_rows([
    ("A", "fill",       10, "0.40", "-4.17", "0.17",  10, "4.00",  0),
    ("B", "fill",       -5, "0.70", "-1.58", "0.08",  -5, "1.50",  0),
    ("A", "fill",       -4, "0.55",  "2.13", "0.07",   6, "2.40", "0.60"),
    ("A", "settlement", -6,  1,       6,      0,       0,  0,     "4.20"),
    ("B", "settlement",  5,  1,       0,      0,       0,  0,     "-1.50"),
])


def run1_rows(cur, run1):
    cur.execute(
        "SELECT market_ticker, kind, delta, yes_price, cash_flow, fee, net_after, "
        "cost_basis_after, realized_cum FROM portfolio_ledger WHERE run_id = %s "
        "ORDER BY ts, seq, market_ticker", (run1,))
    return cur.fetchall()


def check_test1(cur, run1, label):
    actual = run1_rows(cur, run1)
    report(f"{label}1: run 1 ledger is exactly the five expected rows",
           actual == RUN1_EXPECTED, f"actual {actual}")


def check_test6(cur, run2, label):
    cur.execute(
        "SELECT delta, yes_price, cash_flow, net_after, cost_basis_after, realized_cum "
        "FROM portfolio_ledger WHERE run_id = %s ORDER BY ts, seq", (run2,))
    actual = cur.fetchall()
    want = [tuple(Decimal(x) for x in row) for row in [
        ("3", "0.20", "-0.64", "3", "0.60", "0"),
        ("-5", "0.30", "-0.59", "-2", "1.40", "0.30"),
    ]]
    report(f"{label}6: flip yes 3 then no 5: cash -0.64 and -0.59, net -2, basis 1.40, realized 0.30",
           actual == want, f"actual {actual}")


def setup_fixture(cur):
    cur.execute("INSERT INTO underlying (underlying_id, description) VALUES ('NYC', 'New York')")
    cur.execute("INSERT INTO series (ticker, underlying_id) VALUES ('KXHIGHNY', 'NYC')")
    cur.execute(
        "INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source) "
        "VALUES ('EV1', 'KXHIGHNY', 'NYC', '2026-06-03', 'NWS')")
    for ticker in ("A", "B", "C"):
        add_market(cur, ticker)

    run1 = add_run(cur)
    fill(cur, run1, "A", "yes", "buy", 10, "0.40", "0.17", at(1, 14, 1))
    fill(cur, run1, "B", "no", "buy", 5, "0.30", "0.08", at(1, 15, 1))
    fill(cur, run1, "A", "yes", "sell", 4, "0.55", "0.07", at(2, 14, 1))
    refuse(cur, run1, "A", "rejected", "no_quote", at(2, 17, 1))
    refuse(cur, run1, "A", "blocked", "gate_locked", at(2, 18, 1))
    settle(cur, run1, "A", at(4, 13), "yes", 6, 6)
    settle(cur, run1, "B", at(4, 13), "yes", -5, 0)
    return run1


def check_test2(cur, run1):
    cur.execute("SELECT 1000 + sum(cash_flow), sum(fee) FROM portfolio_ledger WHERE run_id = %s",
                (run1,))
    cash, fees = cur.fetchone()
    report("2a: 1000 + sum(cash_flow) = 1002.38", cash == Decimal("1002.38"), f"actual {cash}")
    report("2b: sum(fee) = 0.32", fees == Decimal("0.32"), f"actual {fees}")
    cur.execute(
        "SELECT sum(realized_cum) FROM ("
        "  SELECT DISTINCT ON (market_ticker) market_ticker, realized_cum FROM portfolio_ledger "
        "  WHERE run_id = %s ORDER BY market_ticker, ts DESC, kind DESC, seq DESC) t", (run1,))
    realized = cur.fetchone()[0]
    report("2c: last realized_cum of A plus B = 2.70", realized == Decimal("2.70"),
           f"actual {realized}")


def check_test3(cur, run1):
    broken = identity_failures(cur, run1, Decimal(1000))
    report("3: book-keeping identity holds after every event of run 1", not broken, str(broken))


def check_test4(cur):
    for args, want in [((12, "0.47", "0.07"), "0.21"), ((10, "0.40", "0.07"), "0.17"),
                       ((10, "0.50", "0.04"), "0.10")]:
        cur.execute("SELECT kalshi_fee(%s, %s, %s)", args)
        got = cur.fetchone()[0]
        report(f"4: kalshi_fee{args} = {want}", got == Decimal(want), f"actual {got}")


def check_test5(cur, run1):
    cur.execute("SELECT count(*) FROM fee_check WHERE run_id = %s", (run1,))
    n = cur.fetchone()[0]
    report("5: fee_check has no row for run 1", n == 0, f"actual {n} rows")


def check_test7(cur, run2):
    cur.execute("SELECT fee, expected_fee, market_ticker FROM fee_check WHERE run_id = %s", (run2,))
    rows = cur.fetchall()
    report("7: fee_check has exactly one row for run 2, fee 0.09 expected 0.08",
           rows == [(Decimal("0.09"), Decimal("0.08"), "C")], f"actual {rows}")


def check_test9(cur, run3):
    cur.execute("SELECT count(*), count(expected_fee) FROM fee_check WHERE run_id = %s", (run3,))
    rows, with_fee = cur.fetchone()
    report("9: run with params {} appears in fee_check with expected_fee null",
           (rows, with_fee) == (1, 0), f"actual rows {rows}, non-null expected {with_fee}")


def check_test10(cur, run4):
    rows = ledger(cur, run4)
    net_after = rows[-1][6] if rows else None
    broken = identity_failures(cur, run4, Decimal(1000))
    report("10a: after the sell, net_after = 2", len(rows) == 3 and net_after == Decimal(2),
           f"actual {rows}")
    report("10b: identity holds exactly after every event of run 4", not broken, str(broken))


def check_test13(cur, run5):
    rows = ledger(cur, run5)
    want = expected_rows([
        ("C", "fill", -3, "0.60", "-1.26", "0.06", -3, "1.20", 0),
        ("C", "settlement", 3, 0, 3, 0, 0, 0, "1.80")])
    report("13a: sell 3 yes with nothing held costs 1.26; settling no gives realized 1.80",
           rows == want, f"actual {rows}")
    total = sum(r[4] for r in rows)
    report("13b: total cash change of the run is 1.74", total == Decimal("1.74"), f"actual {total}")


def check_test14(cur, run6):
    rows = ledger(cur, run6)
    net_after = rows[-1][6] if len(rows) == 2 else None
    report("14: settlement net_units 4 against 6 held shows net_after 2",
           net_after == Decimal(2), f"actual {rows}")


def live_state():
    """Row counts of the journals in the live database, and the names of its portfolio views."""
    with admin() as conn:
        cur = conn.cursor()
        counts = []
        for table in ("run", "order_intent", "order_outcome", "settlement"):
            cur.execute(f"SELECT count(*) FROM {table}")
            counts.append(cur.fetchone()[0])
        cur.execute("SELECT viewname FROM pg_views WHERE schemaname = 'public' "
                    "AND (viewname LIKE 'portfolio%' OR viewname = 'fee_check') ORDER BY viewname")
        return counts, [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# EXTRA checks: attacks on the book-keeping (not in the agreed list)
# ---------------------------------------------------------------------------

def extra_rows(cur, label, run_id, want_table):
    actual = ledger(cur, run_id)
    want = expected_rows(want_table)
    report(f"EXTRA {label}: ledger rows", actual == want, f"actual {actual} expected {want}")


def extra_total_cash(cur, label, run_id, want):
    """Flat position at the end: sum of cash_flow = realized - fees (here the hand value)."""
    cur.execute("SELECT sum(cash_flow) FROM portfolio_ledger WHERE run_id = %s", (run_id,))
    got = cur.fetchone()[0]
    cur.execute(
        "SELECT sum(r) - (SELECT sum(fee) FROM portfolio_ledger WHERE run_id = %s) FROM ("
        "  SELECT DISTINCT ON (market_ticker) realized_cum AS r FROM portfolio_ledger "
        "  WHERE run_id = %s ORDER BY market_ticker, ts DESC, kind DESC, seq DESC) t",
        (run_id, run_id))
    realized_minus_fees = cur.fetchone()[0]
    report(f"EXTRA {label}: total cash {want} = realized - fees",
           got == Decimal(want) == realized_minus_fees,
           f"cash {got} hand {want} realized-fees {realized_minus_fees}")


def extra_identity(cur, label, run_id, cash=1000):
    broken = identity_failures(cur, run_id, Decimal(cash))
    report(f"EXTRA {label}: identity holds after every event", not broken, str(broken))


def extra_checks(cur):
    # E1 two fills at the same instant in one market: outcome_id breaks the tie
    add_market(cur, "X1")
    run = add_run(cur)
    t = at(2, 10)
    fill(cur, run, "X1", "yes", "buy", 2, "0.30", "0.01", t)
    fill(cur, run, "X1", "yes", "buy", 3, "0.50", "0.02", t)
    extra_rows(cur, "E1 two fills at one instant", run, [
        ("X1", "fill", 2, "0.30", "-0.61", "0.01", 2, "0.60", 0),
        ("X1", "fill", 3, "0.50", "-1.52", "0.02", 5, "2.10", 0)])
    extra_identity(cur, "E1", run)

    # E2 a fill and a settlement at the same instant: the fill comes first
    add_market(cur, "X2")
    run = add_run(cur)
    fill(cur, run, "X2", "yes", "buy", 4, "0.25", 0, at(3, 12))
    settle(cur, run, "X2", at(3, 12), "yes", 4, 4)
    extra_rows(cur, "E2 fill and settlement at one instant", run, [
        ("X2", "fill", 4, "0.25", "-1.00", 0, 4, "1.00", 0),
        ("X2", "settlement", -4, 1, 4, 0, 0, 0, "3.00")])
    extra_identity(cur, "E2", run)

    # E3 fractional quantities: 2.5 @ 0.40 cost 1.00; sell 1 @ 0.60 releases 0.40; settle 1.5
    add_market(cur, "X3")
    run = add_run(cur)
    fill(cur, run, "X3", "yes", "buy", "2.5", "0.40", "0.02", at(1, 10))
    fill(cur, run, "X3", "yes", "sell", 1, "0.60", 0, at(2, 10))
    settle(cur, run, "X3", at(4, 13), "yes", "1.5", "1.5")
    extra_rows(cur, "E3 fractional quantities", run, [
        ("X3", "fill", "2.5", "0.40", "-1.02", "0.02", "2.5", "1.00", 0),
        ("X3", "fill", -1, "0.60", "0.60", 0, "1.5", "0.60", "0.20"),
        ("X3", "settlement", "-1.5", 1, "1.5", 0, 0, 0, "1.10")])
    extra_identity(cur, "E3", run)

    # E4 a zero-fee fill is a valid fill; fee_check flags it (rate 0.07 expects 0.02)
    add_market(cur, "X4")
    run = add_run(cur)
    fill(cur, run, "X4", "yes", "buy", 1, "0.50", 0, at(1, 10))
    extra_rows(cur, "E4 zero-fee fill", run, [("X4", "fill", 1, "0.50", "-0.50", 0, 1, "0.50", 0)])
    cur.execute("SELECT fee, expected_fee FROM fee_check WHERE run_id = %s", (run,))
    rows = cur.fetchall()
    report("EXTRA E4: zero fee vs rate 0.07 is flagged with expected 0.02",
           rows == [(Decimal(0), Decimal("0.02"))], f"actual {rows}")
    run0 = add_run(cur, {"fee_rate": "0"})
    fill(cur, run0, "X4", "yes", "buy", 1, "0.50", 0, at(1, 10))
    cur.execute("SELECT count(*) FROM fee_check WHERE run_id = %s", (run0,))
    n = cur.fetchone()[0]
    report("EXTRA E4: fee 0 with fee_rate 0 is not flagged", n == 0, f"actual {n} rows")

    # E5 selling NO to reduce a NO position, then closing it
    add_market(cur, "X5")
    run = add_run(cur)
    fill(cur, run, "X5", "no", "buy", 4, "0.30", 0, at(1, 10))
    fill(cur, run, "X5", "no", "sell", 1, "0.50", 0, at(2, 10))
    fill(cur, run, "X5", "no", "sell", 3, "0.20", 0, at(3, 10))
    extra_rows(cur, "E5 sell NO to reduce and close", run, [
        ("X5", "fill", -4, "0.70", "-1.20", 0, -4, "1.20", 0),
        ("X5", "fill", 1, "0.50", "0.50", 0, -3, "0.90", "0.20"),
        ("X5", "fill", 3, "0.80", "0.60", 0, 0, 0, "-0.10")])
    extra_identity(cur, "E5", run)

    # E6 close to exactly zero, reopen on the other side, then settle no
    add_market(cur, "X6")
    run = add_run(cur)
    fill(cur, run, "X6", "yes", "buy", 2, "0.40", 0, at(1, 10))
    fill(cur, run, "X6", "yes", "sell", 2, "0.50", 0, at(2, 10))
    fill(cur, run, "X6", "no", "buy", 3, "0.60", 0, at(3, 10))
    settle(cur, run, "X6", at(4, 13), "no", -3, 3)
    extra_rows(cur, "E6 close to zero and reopen on the other side", run, [
        ("X6", "fill", 2, "0.40", "-0.80", 0, 2, "0.80", 0),
        ("X6", "fill", -2, "0.50", "1.00", 0, 0, 0, "0.20"),
        ("X6", "fill", -3, "0.40", "-1.80", 0, -3, "1.80", "0.20"),
        ("X6", "settlement", 3, 0, 3, 0, 0, 0, "1.40")])
    extra_identity(cur, "E6", run)

    # E7 a NO position that settles no (wins): paid 1.50, payout 5, profit 3.50
    add_market(cur, "X7")
    run = add_run(cur)
    fill(cur, run, "X7", "no", "buy", 5, "0.30", 0, at(1, 10))
    settle(cur, run, "X7", at(4, 13), "no", -5, 5)
    extra_rows(cur, "E7 NO position settles no", run, [
        ("X7", "fill", -5, "0.70", "-1.50", 0, -5, "1.50", 0),
        ("X7", "settlement", 5, 0, 5, 0, 0, 0, "3.50")])
    extra_identity(cur, "E7", run)

    # E8 settlement net_units (4) does not match the fills (6): the ledger follows the settlement
    add_market(cur, "X8")
    run = add_run(cur)
    fill(cur, run, "X8", "yes", "buy", 6, "0.40", 0, at(1, 10))
    settle(cur, run, "X8", at(4, 13), "yes", 4, 4)
    extra_rows(cur, "E8 settlement net_units mismatch", run, [
        ("X8", "fill", 6, "0.40", "-2.40", 0, 6, "2.40", 0),
        ("X8", "settlement", -4, 1, 4, 0, 2, "0.80", "2.40")])
    extra_identity(cur, "E8", run)

    # E9 many partial closes that do not divide evenly: buys cost 0.99 + 1.64 = 2.63
    add_market(cur, "X9")
    run = add_run(cur)
    fill(cur, run, "X9", "yes", "buy", 3, "0.33", "0.01", at(1, 10))
    fill(cur, run, "X9", "yes", "buy", 4, "0.41", "0.01", at(1, 11))
    prices = ["0.41", "0.43", "0.39", "0.52", "0.44", "0.46", "0.50"]
    for n, price in enumerate(prices):
        fill(cur, run, "X9", "yes", "sell", 1, price, "0.01", at(2, 10 + n))
    rows = ledger(cur, run)
    nets = [r[6] for r in rows]
    report("EXTRA E9: net_after counts 3,7,6,5,4,3,2,1,0 over the nine fills",
           nets == [Decimal(x) for x in (3, 7, 6, 5, 4, 3, 2, 1, 0)], f"actual {nets}")
    cash = [r[4] for r in rows]
    want_cash = [Decimal(x) for x in ("-1.00", "-1.65", "0.40", "0.42", "0.38", "0.51", "0.43",
                                      "0.45", "0.49")]
    report("EXTRA E9: cash_flow of the nine fills (buys -cost-fee, sells price-fee)",
           cash == want_cash, f"actual {cash}")
    want_realized = sum(Decimal(p) for p in prices) - Decimal("2.63")
    report("EXTRA E9: final realized = proceeds - total cost = 0.52 exactly, basis 0",
           rows[-1][8] == want_realized == Decimal("0.52") and rows[-1][7] == 0,
           f"actual realized {rows[-1][8]} basis {rows[-1][7]}")
    extra_identity(cur, "E9", run)

    # E10 sell with no holding opens NO at 1-p: cash -1.20. Sell 5 yes holding 2: close 2 (+1.20)
    # and open 3 NO at 0.40 (-1.20), cash 0.
    add_market(cur, "X10")
    run = add_run(cur)
    fill(cur, run, "X10", "yes", "sell", 3, "0.60", 0, at(1, 10))
    extra_rows(cur, "E10 sell yes with no holding (cash -1.20)", run, [
        ("X10", "fill", -3, "0.60", "-1.20", 0, -3, "1.20", 0)])
    extra_identity(cur, "E10 sell yes with no holding", run)
    add_market(cur, "X10b")
    run = add_run(cur)
    fill(cur, run, "X10b", "yes", "buy", 2, "0.40", 0, at(1, 10))
    fill(cur, run, "X10b", "yes", "sell", 5, "0.60", 0, at(2, 10))
    extra_rows(cur, "E10b sell 5 yes holding 2 (cash 0)", run, [
        ("X10b", "fill", 2, "0.40", "-0.80", 0, 2, "0.80", 0),
        ("X10b", "fill", -5, "0.60", "0.00", 0, -3, "1.20", "0.40")])
    extra_identity(cur, "E10b sell 5 yes holding 2", run)

    # E11 kalshi_fee: rounding and edge cases
    for args, want in [((1, "0.50", "0.07"), "0.02"), ((100, "0.50", "0.07"), "1.75"),
                       ((1, "0.01", "0.07"), "0.01"), ((0, "0.50", "0.07"), "0.00"),
                       ((2.5, "0.50", "0.07"), "0.05"), ((10, "0.50", "0"), "0.00")]:
        cur.execute("SELECT kalshi_fee(%s::numeric, %s::numeric, %s::numeric)", args)
        got = cur.fetchone()[0]
        report(f"EXTRA E11: kalshi_fee{args} = {want}", got == Decimal(want), f"actual {got}")

    # E12 fee_check: a smaller stored fee is flagged; rejected outcomes in a {} run are not
    add_market(cur, "X12")
    run = add_run(cur)
    fill(cur, run, "X12", "yes", "buy", 10, "0.40", "0.16", at(1, 10))
    cur.execute("SELECT fee, expected_fee FROM fee_check WHERE run_id = %s", (run,))
    rows = cur.fetchall()
    report("EXTRA E12: stored fee 0.16 vs expected 0.17 is flagged",
           rows == [(Decimal("0.16"), Decimal("0.17"))], f"actual {rows}")
    run = add_run(cur, {})
    refuse(cur, run, "X12", "rejected", "no_quote", at(1, 10))
    cur.execute("SELECT count(*) FROM fee_check WHERE run_id = %s", (run,))
    n = cur.fetchone()[0]
    report("EXTRA E12: a rejected outcome never appears in fee_check", n == 0, f"actual {n} rows")

    # E14 buy NO while long YES: partial close, exact close (round trip with fees)
    add_market(cur, "X14")
    run = add_run(cur)
    fill(cur, run, "X14", "yes", "buy", 5, "0.40", "0.03", at(1, 10))
    fill(cur, run, "X14", "no", "buy", 2, "0.70", "0.02", at(2, 10))
    fill(cur, run, "X14", "no", "buy", 3, "0.80", "0.01", at(3, 10))
    extra_rows(cur, "E14 buy NO while long YES: partial then exact close", run, [
        ("X14", "fill", 5, "0.40", "-2.03", "0.03", 5, "2.00", 0),
        ("X14", "fill", -2, "0.30", "0.58", "0.02", 3, "1.20", "-0.20"),
        ("X14", "fill", -3, "0.20", "0.59", "0.01", 0, 0, "-0.80")])
    extra_total_cash(cur, "E14 round trip", run, "-0.86")

    # E15 buy NO while long YES: flip, then settle yes. Close 2 YES (+1.40), open 4 NO (-1.20)
    add_market(cur, "X15")
    run = add_run(cur)
    fill(cur, run, "X15", "yes", "buy", 2, "0.50", "0.01", at(1, 10))
    fill(cur, run, "X15", "no", "buy", 6, "0.30", "0.02", at(2, 10))
    settle(cur, run, "X15", at(4, 13), "yes", -4, 0)
    extra_rows(cur, "E15 buy NO flips a long YES", run, [
        ("X15", "fill", 2, "0.50", "-1.01", "0.01", 2, "1.00", 0),
        ("X15", "fill", -6, "0.70", "0.18", "0.02", -4, "1.20", "0.40"),
        ("X15", "settlement", 4, 1, 0, 0, 0, 0, "-0.80")])
    extra_total_cash(cur, "E15 round trip", run, "-0.83")

    # E16 sell NO with nothing held opens YES at 0.70 (-2.80); settle yes pays 4
    add_market(cur, "X16")
    run = add_run(cur)
    fill(cur, run, "X16", "no", "sell", 4, "0.30", 0, at(1, 10))
    settle(cur, run, "X16", at(4, 13), "yes", 4, 4)
    extra_rows(cur, "E16 sell NO with nothing held", run, [
        ("X16", "fill", 4, "0.70", "-2.80", 0, 4, "2.80", 0),
        ("X16", "settlement", -4, 1, 4, 0, 0, 0, "1.20")])
    extra_total_cash(cur, "E16 round trip", run, "1.20")

    # E17 sell NO beyond the holding: close 2 NO (+0.80), open 3 YES at 0.60 (-1.80)
    add_market(cur, "X17")
    run = add_run(cur)
    fill(cur, run, "X17", "no", "buy", 2, "0.50", 0, at(1, 10))
    fill(cur, run, "X17", "no", "sell", 5, "0.40", 0, at(2, 10))
    extra_rows(cur, "E17 sell NO beyond the holding", run, [
        ("X17", "fill", -2, "0.50", "-1.00", 0, -2, "1.00", 0),
        ("X17", "fill", 5, "0.60", "-1.00", 0, 3, "1.80", "-0.20")])
    extra_identity(cur, "E17", run)

    # E18 same instant, opposite sides: buy 3 yes then buy 2 no (outcome_id order)
    add_market(cur, "X18")
    run = add_run(cur)
    t = at(2, 10)
    fill(cur, run, "X18", "yes", "buy", 3, "0.40", 0, t)
    fill(cur, run, "X18", "no", "buy", 2, "0.70", 0, t)
    extra_rows(cur, "E18 same instant, opposite sides", run, [
        ("X18", "fill", 3, "0.40", "-1.20", 0, 3, "1.20", 0),
        ("X18", "fill", -2, "0.30", "0.60", 0, 1, "0.40", "-0.20")])

    # E19 fractional flip: close 1.5 YES (+0.45), open 1 NO at 0.70 (-0.70)
    add_market(cur, "X19")
    run = add_run(cur)
    fill(cur, run, "X19", "yes", "buy", "1.5", "0.40", 0, at(1, 10))
    fill(cur, run, "X19", "no", "buy", "2.5", "0.70", 0, at(2, 10))
    extra_rows(cur, "E19 fractional flip", run, [
        ("X19", "fill", "1.5", "0.40", "-0.60", 0, "1.5", "0.60", 0),
        ("X19", "fill", "-2.5", "0.30", "-0.25", 0, -1, "0.70", "-0.15")])

    # E20 two runs share a market: run B's sell with nothing held must not close run A's YES
    add_market(cur, "X20")
    run_a = add_run(cur)
    run_b = add_run(cur)
    fill(cur, run_a, "X20", "yes", "buy", 2, "0.40", 0, at(1, 10))
    fill(cur, run_b, "X20", "yes", "sell", 2, "0.40", 0, at(1, 10))
    extra_rows(cur, "E20 run A in a shared market", run_a, [
        ("X20", "fill", 2, "0.40", "-0.80", 0, 2, "0.80", 0)])
    extra_rows(cur, "E20 run B sells with nothing held", run_b, [
        ("X20", "fill", -2, "0.40", "-1.20", 0, -2, "1.20", 0)])

    # E13 two runs, same market, same instant: states stay apart
    add_market(cur, "X13")
    run_a = add_run(cur)
    run_b = add_run(cur)
    fill(cur, run_a, "X13", "yes", "buy", 2, "0.40", 0, at(1, 10))
    fill(cur, run_b, "X13", "no", "buy", 2, "0.40", 0, at(1, 10))
    extra_rows(cur, "E13 run A in a shared market", run_a, [
        ("X13", "fill", 2, "0.40", "-0.80", 0, 2, "0.80", 0)])
    extra_rows(cur, "E13 run B in a shared market", run_b, [
        ("X13", "fill", -2, "0.60", "-0.80", 0, -2, "0.80", 0)])


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

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
            run1 = setup_fixture(cur)

            check_test1(cur, run1, "")
            check_test2(cur, run1)
            check_test3(cur, run1)
            check_test4(cur)
            check_test5(cur, run1)

            run2 = add_run(cur)
            fill(cur, run2, "C", "yes", "buy", 3, "0.20", "0.04", at(2, 15, 1))
            fill(cur, run2, "C", "no", "buy", 5, "0.70", "0.09", at(2, 16, 1))
            check_test6(cur, run2, "")
            check_test7(cur, run2)
            check_test1(cur, run1, "8 after run 2: ")

            run3 = add_run(cur, {})
            fill(cur, run3, "C", "yes", "buy", 1, "0.50", "0.02", at(2, 14))
            check_test9(cur, run3)

            run4 = add_run(cur)
            fill(cur, run4, "C", "yes", "buy", 1, "0.40", "0.02", at(2, 10))
            fill(cur, run4, "C", "yes", "buy", 2, "0.41", "0.04", at(2, 11))
            fill(cur, run4, "C", "yes", "sell", 1, "0.50", "0.02", at(2, 12))
            check_test10(cur, run4)

            run5 = add_run(cur)
            fill(cur, run5, "C", "yes", "sell", 3, "0.60", "0.06", at(2, 10))
            settle(cur, run5, "C", at(4, 13), "no", -3, 3)
            check_test13(cur, run5)

            run6 = add_run(cur)
            fill(cur, run6, "C", "yes", "buy", 6, "0.40", "0.11", at(2, 10))
            settle(cur, run6, "C", at(4, 13), "yes", 4, 4)
            check_test14(cur, run6)

            cur.execute(PORTFOLIO_SQL)
            report("11a: applying db/portfolio.sql a second time succeeds", True)
            check_test1(cur, run1, "11b: ")
            check_test6(cur, run2, "11c: ")

            extra_checks(cur)
    finally:
        with admin() as conn:
            conn.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")

    after_counts, after_view = live_state()
    report("12a: live database journals have the same row counts", before_counts == after_counts,
           f"before {before_counts} after {after_counts}")
    report("12b: live portfolio views unchanged by the script",
           before_view == after_view, f"before {before_view} after {after_view}")

    print(f"{len(failures)} failure(s)")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
