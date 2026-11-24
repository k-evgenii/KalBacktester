"""Check one run against the portfolio's own recomputation (spec C03).

Run from the repo root: uv run python -m portfolio.check_run <run_id> [--db NAME]
Reads fee_check, portfolio_snapshot_check and portfolio_settlement_check (db/portfolio.sql).
SELECT only; prints one line per check; exits 0 on PASS, 1 on FAIL, 2 on an error.
"""
import argparse
import sys

import psycopg

from db.connect import connect

CHECK_VIEWS = [
    ("fees", "fee_check"),
    ("snapshots", "portfolio_snapshot_check"),
    ("settlements", "portfolio_settlement_check"),
]
MAX_ROWS_SHOWN = 10


def check_run(conn, run_id):
    """Returns [(name, ok, rows)] for fees, snapshots and settlements of the run."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM run WHERE run_id = %s", (run_id,))
    if cur.fetchone() is None:
        raise ValueError(f"unknown run_id {run_id}")
    cur.execute("SELECT to_regclass('portfolio_snapshot_check')")
    if cur.fetchone()[0] is None:
        cur.execute("SELECT current_database()")
        raise RuntimeError(f"db/portfolio.sql is not applied to database {cur.fetchone()[0]}")

    results = []
    for name, view in CHECK_VIEWS:
        cur.execute(f"SELECT * FROM {view} WHERE run_id = %s ORDER BY 1, 2, 3", (run_id,))
        rows = cur.fetchall()
        results.append((name, not rows, rows))
    return results


def main():
    parser = argparse.ArgumentParser(description="Check one run against the portfolio.")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()
    try:
        conn = connect(args.db)
        try:
            results = check_run(conn, args.run_id)
        finally:
            conn.close()
    except (ValueError, RuntimeError, psycopg.Error) as error:
        print(error)
        sys.exit(2)

    for name, ok, rows in results:
        if ok:
            print(f"[PASS] {name}")
        else:
            print(f"[FAIL] {name} ({len(rows)} rows)")
            for row in rows[:MAX_ROWS_SHOWN]:
                print(f"  {row}")
    all_ok = all(ok for _, ok, _ in results)
    print("PASS" if all_ok else "FAIL")
    sys.exit(0 if all_ok else 1)


if __name__ == "__main__":
    main()
