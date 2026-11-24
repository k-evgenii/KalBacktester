"""Check for spec A024, agreed test 3: saved runs can be deleted child-first, never updated.

Run from the repo root: uv run python -m tests.db.check_deletable_runs
Restores a backup into the throwaway database kalbt_deletable_test (dropped at the end),
applies the pending migrations there, deletes run 31's rows, then tries to UPDATE run.strategy.
Two backups are used: the newest one in backups/db, and the pinned "before-0004" one in backups/pinned
(made before the migration; backups/db rotates, so it keeps a permanent copy).
Live is never written to.
"""
import subprocess

import psycopg

from db import backup, migrate
from db.connect import connect

TEST_DB = "kalbt_deletable_test"
PINNED_DIR = backup.BACKUP_DIR.parent / "pinned"
RUN_ID = 31

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + str(detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def drop_db():
    subprocess.run(["docker", "exec", backup.CONTAINER, "sh", "-c",
                    f'dropdb --if-exists -U "$POSTGRES_USER" {TEST_DB}'], capture_output=True)


def count(conn, sql):
    return conn.execute(sql, (RUN_ID,)).fetchone()[0]


def check_backup(label, dump_file):
    backup.restore(dump_file, TEST_DB)
    migrate.main(TEST_DB)  # applies 0004 if the backup predates it; nothing otherwise
    with connect(TEST_DB) as conn:
        has_run = count(conn, "SELECT count(*) FROM run WHERE run_id = %s") == 1
        report(f"{label}: run {RUN_ID} is in the backup", has_run)
        if not has_run:
            return
        before = {t: count(conn, f"SELECT count(*) FROM {t} WHERE run_id = %s")
                  for t in ["settlement", "position_snapshot", "order_intent"]}
        before_outcome = count(conn, """SELECT count(*) FROM order_outcome
                                        WHERE intent_id IN (SELECT intent_id FROM order_intent WHERE run_id = %s)""")
        report(f"{label}: run {RUN_ID} has child rows to delete", sum(before.values()) > 0, before)

        conn.execute("""DELETE FROM order_outcome
                        WHERE intent_id IN (SELECT intent_id FROM order_intent WHERE run_id = %s)""", (RUN_ID,))
        for table in ["settlement", "position_snapshot", "order_intent", "run"]:
            conn.execute(f"DELETE FROM {table} WHERE run_id = %s", (RUN_ID,))
        conn.commit()
        left = {t: count(conn, f"SELECT count(*) FROM {t} WHERE run_id = %s")
                for t in ["settlement", "position_snapshot", "order_intent", "run"]}
        report(f"{label}: run {RUN_ID} and its rows are deleted (outcomes were {before_outcome})",
               all(n == 0 for n in left.values()), left)

        # the other saved run stays and still cannot be edited
        other = conn.execute("SELECT run_id FROM run ORDER BY run_id LIMIT 1").fetchone()
        report(f"{label}: another run is still there", other is not None)
        try:
            conn.execute("UPDATE run SET strategy = 'x'")
            refused = False
        except psycopg.errors.RestrictViolation:
            refused = True
        conn.rollback()
        report(f"{label}: UPDATE run SET strategy = 'x' is refused", refused)
        try:
            conn.execute("UPDATE order_intent SET quantity = quantity + 1")
            refused = False
        except psycopg.errors.RestrictViolation:
            refused = True
        conn.rollback()
        report(f"{label}: UPDATE on order_intent is refused", refused)


try:
    dumps = sorted(backup.BACKUP_DIR.glob("*.dump"))
    newest = dumps[-1]
    check_backup("newest backup", newest)
    pre = sorted(PINNED_DIR.glob("*before-0004*.dump"))
    if pre:
        check_backup("before-0004 backup", pre[-1])
    else:
        report("a pinned before-0004 backup exists in backups/pinned", False)
finally:
    drop_db()

if failures:
    print(f"\n{len(failures)} check(s) FAILED: {failures}")
    raise SystemExit(1)
print("\nall deletable-run checks passed")
