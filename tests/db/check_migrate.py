"""Checks migrations and backups (spec A018, agreed tests 1 to 6, plus extras).

Run from the repo root: uv run python -m tests.db.check_migrate
Throwaway databases kalbt_migrate_test and kalbt_restore_test are dropped at the end.
The live database kalbt is only read (row counts) and dumped.
"""
import contextlib
import hashlib
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from db import backup, migrate
from db.connect import connect

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
REAL_MIGRATIONS = ROOT / "db" / "migrations"
TEST_DB = "kalbt_migrate_test"
RESTORE_DB = "kalbt_restore_test"

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + str(detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def docker_sh(command, stdin=None):
    return subprocess.run(["docker", "exec", "-i", backup.CONTAINER, "sh", "-c", command],
                          stdin=stdin, capture_output=True, text=True)


def drop_db(name):
    docker_sh(f'dropdb --if-exists -U "$POSTGRES_USER" {name}')


def build_test_db():
    drop_db(TEST_DB)
    made = docker_sh(f'createdb -U "$POSTGRES_USER" {TEST_DB}')
    assert made.returncode == 0, made.stderr
    with open(SCHEMA, "rb") as f:
        loaded = docker_sh(f'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d {TEST_DB}', stdin=f)
    assert loaded.returncode == 0, loaded.stderr


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def run_migrate(dbname=TEST_DB):
    """Run migrate.main in-process. Returns (stdout, exit message or None)."""
    out = io.StringIO()
    exit_message = None
    with contextlib.redirect_stdout(out):
        try:
            migrate.main(dbname)
        except SystemExit as e:
            exit_message = str(e.code)
    return out.getvalue(), exit_message


def query(dbname, sql, params=None):
    with connect(dbname) as conn:
        return conn.execute(sql, params).fetchall()


def table_counts(dbname):
    tables = [r[0] for r in query(dbname, "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1")]
    return {t: query(dbname, f'SELECT count(*) FROM "{t}"')[0][0] for t in tables}


def make_dump_file():
    return backup.dump("qa-test")


original_dir = migrate.MIGRATIONS_DIR
original_backup_dir = backup.BACKUP_DIR
tmp = Path(tempfile.mkdtemp(prefix="kalbt_migrate_qa_"))
try:
    build_test_db()

    # ---- 1. fresh build records the baseline; CLI says nothing to apply
    rows = dict(query(TEST_DB, "SELECT id, checksum FROM schema_migration"))
    report("1a fresh build records 0001_baseline with the file checksum",
           rows == {"0001_baseline": sha(REAL_MIGRATIONS / "0001_baseline.sql")}, rows)
    cli = subprocess.run(["uv", "run", "python", "-m", "db.migrate", "--dbname", TEST_DB],
                         cwd=ROOT, capture_output=True, text=True)
    report("1b CLI migrate on fresh build applies 0002 (data migration, not in schema.sql)",
           cli.returncode == 0 and "applied 0002_us_daily_high_stations" in cli.stdout,
           (cli.returncode, cli.stdout, cli.stderr[-300:]))
    cli2 = subprocess.run(["uv", "run", "python", "-m", "db.migrate", "--dbname", TEST_DB],
                          cwd=ROOT, capture_output=True, text=True)
    report("1b CLI migrate a second time prints 'nothing to apply'",
           cli2.returncode == 0 and "nothing to apply" in cli2.stdout, (cli2.returncode, cli2.stdout, cli2.stderr[-300:]))
    report("1c the real migrations folder holds exactly 0001_baseline.sql, 0002_us_daily_high_stations.sql and 0003_portfolio_views.sql",
           sorted(p.name for p in REAL_MIGRATIONS.glob("*.sql")) ==
           ["0001_baseline.sql", "0002_us_daily_high_stations.sql", "0003_portfolio_views.sql"])

    # ---- 2. extra migration applied once
    copy_dir = tmp / "migrations_copy"
    shutil.copytree(REAL_MIGRATIONS, copy_dir)
    (copy_dir / "9999_test.sql").write_text("CREATE TABLE qa_migrate_probe (x int);\n")
    migrate.MIGRATIONS_DIR = copy_dir
    out1, err1 = run_migrate()
    report("2a first run applies 9999_test", err1 is None and "applied 9999_test" in out1, (out1, err1))
    recorded = dict(query(TEST_DB, "SELECT id, checksum FROM schema_migration"))
    report("2b 9999_test recorded with its checksum", recorded.get("9999_test") == sha(copy_dir / "9999_test.sql"), recorded)
    report("2c probe table exists", query(TEST_DB, "SELECT to_regclass('public.qa_migrate_probe')")[0][0] is not None)
    out2, err2 = run_migrate()
    report("2d second run applies nothing", err2 is None and "applied" not in out2 and "nothing to apply" in out2, (out2, err2))
    report("2e still exactly one record of 9999_test",
           query(TEST_DB, "SELECT count(*) FROM schema_migration WHERE id = '9999_test'")[0][0] == 1)

    # ---- 3. checksum mismatch stops and applies nothing
    build_test_db()
    (copy_dir / "9999_test.sql").write_text("CREATE TABLE qa_migrate_probe (x int);\n")
    with connect(TEST_DB) as conn:
        conn.execute("UPDATE schema_migration SET checksum = 'tampered' WHERE id = '0001_baseline'")
    out3, err3 = run_migrate()
    report("3a mismatch stops with an error", err3 is not None and "0001_baseline" in err3, (out3, err3))
    report("3b nothing applied", "applied" not in out3
           and query(TEST_DB, "SELECT to_regclass('public.qa_migrate_probe')")[0][0] is None
           and query(TEST_DB, "SELECT count(*) FROM schema_migration")[0][0] == 1)
    migrate.MIGRATIONS_DIR = original_dir

    # ---- 4. dump then restore: same row counts as live
    backup.BACKUP_DIR = tmp / "backups_test4"
    before = table_counts("kalbt")
    dump_file = make_dump_file()
    after = table_counts("kalbt")
    report("4a live counts did not move during the dump (comparison is valid)", before == after, (before, after))
    report("4b dump file exists and is not empty", dump_file.exists() and dump_file.stat().st_size > 0)
    backup.restore(dump_file, RESTORE_DB)
    restored = table_counts(RESTORE_DB)
    report("4c restored row counts equal live for every table", restored == before,
           {t: (before.get(t), restored.get(t)) for t in set(before) | set(restored) if before.get(t) != restored.get(t)})
    report("4d restored database has tables", len(restored) >= 15, len(restored))

    # ---- 5. restore into the live name is refused
    try:
        backup.restore(dump_file, "kalbt")
        refused = False
    except Exception:
        refused = True
    report("5 restore(..., 'kalbt') is refused", refused)
    report("5b live database still has its tables", table_counts("kalbt") == before)

    # ---- 6. rotation in a temporary backup directory
    backup.BACKUP_DIR = tmp / "backups"
    backup.BACKUP_DIR.mkdir()
    for i in range(12):
        (backup.BACKUP_DIR / f"2020010{i // 10}T0000{i % 10}0Z_old{i}.dump").write_bytes(b"x")
    new_file = backup.dump("rotation")
    left = sorted(p.name for p in backup.BACKUP_DIR.glob("*.dump"))
    report("6a only 10 dumps remain", len(left) == 10, left)
    report("6b the new dump is kept", new_file.name in left)
    report("6c the oldest were removed, newest kept",
           left == sorted(left) and "2020010" + "0" + "00" + "00Z_old0.dump" not in left
           and any(n.endswith("_old11.dump") for n in left), left)
finally:
    migrate.MIGRATIONS_DIR = original_dir
    backup.BACKUP_DIR = original_backup_dir
    drop_db(TEST_DB)
    drop_db(RESTORE_DB)
    shutil.rmtree(tmp, ignore_errors=True)

if failures:
    print(f"\n{len(failures)} check(s) FAILED: {failures}")
    raise SystemExit(1)
print("\nall migrate and backup checks passed")
