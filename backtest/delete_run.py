"""Delete saved backtest runs and all their rows (spec B020).

Run from the repo root: uv run python -m backtest.delete_run RUN_ID [RUN_ID ...] --db NAME
All listed runs go in one transaction, or none does. Market, candle and weather data are
never touched.
"""
import argparse
import sys

import psycopg

from db.connect import connect


def delete_runs(conn, run_ids):
    """Delete the runs and their rows. Returns the rows deleted per table, in delete order.

    Raises ValueError, before any delete, if a run id does not exist. Does not commit.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT run_id FROM run WHERE run_id = ANY(%s)", (run_ids,))
        missing = sorted(set(run_ids) - {r[0] for r in cur.fetchall()})
        if missing:
            raise ValueError(f"no such run: {missing}")
        statements = [
            ("order_outcome", ("DELETE FROM order_outcome WHERE intent_id IN "
                               "(SELECT intent_id FROM order_intent WHERE run_id = ANY(%s))")),
            ("order_intent", "DELETE FROM order_intent WHERE run_id = ANY(%s)"),
            ("settlement", "DELETE FROM settlement WHERE run_id = ANY(%s)"),
            ("position_snapshot", "DELETE FROM position_snapshot WHERE run_id = ANY(%s)"),
            ("run", "DELETE FROM run WHERE run_id = ANY(%s)"),
        ]
        counts = {}
        for table, sql in statements:
            cur.execute(sql, (run_ids,))
            counts[table] = cur.rowcount
        return counts


def main():
    parser = argparse.ArgumentParser(description="Delete saved backtest runs and all their rows.")
    parser.add_argument("run_ids", nargs="+", type=int, help="run ids to delete")
    parser.add_argument("--db", required=True, help="database name")
    args = parser.parse_args()

    with connect(args.db) as conn:
        try:
            counts = delete_runs(conn, args.run_ids)
            conn.commit()
        except (ValueError, psycopg.Error) as error:
            conn.rollback()
            print(error)
            sys.exit(1)
    for table, count in counts.items():
        print(f"{table}: {count}")
    print(f"deleted runs {args.run_ids}")


if __name__ == "__main__":
    main()
