"""Dry run of the first live run (spec B014, agreed tests 1 to 5).

Creates a throwaway database kalbt_b014_dry, restores the most recent dump into it, runs
match_intents with backtest/b014_intents.csv there, checks the outcomes and settlements
against the table in the spec, runs the reconciliation, and drops the database in a finally.
The live database kalbt is only read (row counts) and never written.
"""
import subprocess
import sys
from decimal import Decimal
from pathlib import Path

import psycopg

from db.connect import connect, read_password
from engine.match_intents import match_intents, read_csv
from recon.check_run import check_run

ROOT = Path(__file__).resolve().parent.parent.parent
CSV = ROOT / "backtest" / "b014_intents.csv"
DUMPS = Path("/home/kondr/KalBacktester/backups/db")
DRY_DB = "kalbt_b014_dry"
TABLES = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]

D = Decimal
EV = "KXHIGHNY-26NOV18-"
B64, B62, B60, T65 = EV + "B64.5", EV + "B62.5", EV + "B60.5", EV + "T65"

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def live_counts():
    with connect() as conn:
        cur = conn.cursor()
        counts = {}
        for table in TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
    return counts


def admin_connection():
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="kalbt", user="kalbt",
                           password=read_password(), autocommit=True)


def create_dry_database():
    with admin_connection() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DRY_DB}")
        admin.execute(f"CREATE DATABASE {DRY_DB}")


def drop_dry_database():
    with admin_connection() as admin:
        admin.execute(f"DROP DATABASE IF EXISTS {DRY_DB} WITH (FORCE)")


def restore_latest_dump():
    dump = sorted(DUMPS.glob("*.dump"))[-1]
    print(f"restoring {dump.name}")
    with open(dump, "rb") as f:
        result = subprocess.run(
            ["docker", "exec", "-i", "kalbt-recon-db", "sh", "-c",
             f'pg_restore --no-owner -U "$POSTGRES_USER" -d {DRY_DB}'],
            stdin=f, capture_output=True)
    stderr = result.stderr.decode(errors="replace")
    print(f"pg_restore exit code {result.returncode}")
    if stderr.strip():
        print("pg_restore printed:\n" + stderr[:2000])
    if result.returncode != 0:
        raise RuntimeError("pg_restore failed")


def refused(outcome, reason_code):
    return (outcome, reason_code, D(0))


def fill(quantity):
    return ("filled", None, D(quantity))


# One list per intent, in CSV order: (outcome, reason_code, quantity) rows, in outcome order.
EXPECTED = [
    (B64, [refused("rejected", "market_not_open")]),                        # 1
    (B64, [fill(50)]),                                                      # 2
    (B62, [fill(71), refused("rejected", "volume_cap")]),                   # 3
    (B60, [fill(20)]),                                                      # 4
    (T65, [refused("rejected", "nothing_to_sell")]),                        # 5
    (B62, [fill(71)]),                                                      # 6
    (B64, [fill(10)]),                                                      # 7
    (B60, [refused("blocked", "opposite_position")]),                       # 8
    (B64, [refused("blocked", "after_daily_high")]),                        # 9
]


def check_outcomes(cur, run_id):
    cur.execute("SELECT intent_id, market_ticker FROM order_intent WHERE run_id = %s ORDER BY intent_id",
                (run_id,))
    intents = cur.fetchall()
    report("2a. the run has 9 intents on the expected markets",
           [t for _, t in intents] == [t for t, _ in EXPECTED],
           f"got {[t for _, t in intents]}")
    for number, ((intent_id, ticker), (_, expected)) in enumerate(zip(intents, EXPECTED), start=1):
        cur.execute("SELECT outcome, reason_code, quantity, price, fee FROM order_outcome "
                    "WHERE intent_id = %s ORDER BY outcome_id", (intent_id,))
        rows = cur.fetchall()
        actual = [(o, r, q) for o, r, q, _, _ in rows]
        report(f"2.{number} intent {number} outcome rows", actual == expected,
               f"expected {expected}, got {rows}")
        for o, _, q, price, fee in rows:
            if o == "filled":
                print(f"       intent {number} filled {q} at price {price}, fee {fee}")


def check_settlements(cur, run_id):
    cur.execute("SELECT market_ticker, result, net_units, payout FROM settlement "
                "WHERE run_id = %s ORDER BY market_ticker", (run_id,))
    rows = cur.fetchall()
    expected = [(B60, "no", D(-20), D(20)), (B64, "yes", D(60), D(60))]
    report("3. settlements: B64.5 net 60 payout 60, B60.5 net -20 payout 20 (and nothing else)",
           rows == expected, f"expected {expected}, got {rows}")


def main():
    before = live_counts()
    intents = read_csv(CSV)
    report("0. the CSV has 9 intents", len(intents) == 9, f"got {len(intents)}")
    try:
        create_dry_database()
        restore_latest_dump()
        with connect(dbname=DRY_DB) as conn:
            run_id = None
            try:
                run_id = match_intents(conn, "b014_first_live_run", {}, intents)
            except Exception as error:
                report("1. match_intents on the dry database returns a run id", False, repr(error))
            else:
                report("1. match_intents on the dry database returns a run id", isinstance(run_id, int))
            if run_id is not None:
                cur = conn.cursor()
                check_outcomes(cur, run_id)
                check_settlements(cur, run_id)
                result = check_run(conn, run_id)
                for check in result["checks"]:
                    print(f"       check {check['id']} {check['name']}: {check['status']}")
                bad = [c for c in result["checks"] if c["status"] != "pass"]
                report("4. check_run verdict PASS with all 13 checks passing",
                       result["verdict"] == "PASS" and len(result["checks"]) == 13 and not bad,
                       f"verdict {result['verdict']}, failing {bad}")
                print(f"       counts {result['counts']}")
    finally:
        drop_dry_database()
    after = live_counts()
    report("5. live trading tables have the same row counts before and after",
           before == after, f"before {before}, after {after}")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
