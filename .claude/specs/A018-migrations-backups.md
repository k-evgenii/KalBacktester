# A018 — Migrations and database backups

Track: A. Status: APPROVED (overnight plan, `.claude/overnight/PLAYBOOK.md` §7, user 2026-11-24).

## Goal
Give the database a safe way to change and to be restored: numbered migration files applied once
by a small runner that takes a backup first, and a backup script that dumps, rotates and restores
into a throwaway database. Also make the gate tests' three real-dump paths consistent.

## Not built
- No schema change beyond the `schema_migration` table and the baseline record.
- No restore into the live database (`kalbt`): the restore command refuses that name.

## Files
- `db/backup.py` (new), `db/migrate.py` (new), `db/migrations/0001_baseline.sql` (new)
- `db/schema.sql` — append the `schema_migration` table and the baseline row at the end
- `db/README.md` — a short "Migrations and backups" section before `## Usage`
- `tests/ingest/check_qa_kxhighny_dump.py` — only the `JUNE_DUMP` / `AUGUST_DUMP` constants:
  absolute paths under `/home/kondr/KalBacktester/api_probe/kalshi/`, like `DUMP`

## Hard parts (use as written)

`db/schema.sql`, appended:
```sql
-- ---------------------------------------------------------------------------
-- Applied migrations (unit A018). A fresh build from this file already contains every
-- migration listed here; db/migrate.py applies the rest to an existing database.
-- ---------------------------------------------------------------------------
CREATE TABLE schema_migration (
    id         text        PRIMARY KEY,          -- file name without .sql, e.g. 0001_baseline
    checksum   text        NOT NULL,             -- sha256 of the file's bytes
    applied_at timestamptz NOT NULL DEFAULT now()
);
INSERT INTO schema_migration (id, checksum) VALUES ('0001_baseline', '<sha256 of 0001_baseline.sql>');
```
(The implementer fills in the real sha256 after writing the file.)

`db/migrations/0001_baseline.sql`:
```sql
-- Baseline: the schema as of tag night/start (db/schema.sql at commit 7585317).
-- Nothing to apply; recording it marks the starting point for later migrations.
SELECT 1;
```

`db/backup.py` (plain functions; runs `docker exec kalbt-recon-db …` through `subprocess`, using
the container's own `$POSTGRES_USER`; never prints the password):
```
BACKUP_DIR = /home/kondr/KalBacktester/backups/db ; KEEP = 10
dump(reason) -> Path
    file = BACKUP_DIR / f"{UTC now:%Y%m%dT%H%M%SZ}_{reason}.dump"
    pg_dump -Fc of database kalbt, written to file; fail if pg_dump fails or the file is empty
    delete the oldest *.dump files beyond KEEP; return file
restore(path, dbname)
    refuse if dbname == "kalbt"
    dropdb --if-exists dbname; createdb dbname; pg_restore --no-owner -d dbname < path
CLI: python -m db.backup dump --reason R | python -m db.backup restore FILE --dbname NAME
```

`db/migrate.py`:
```
python -m db.migrate [--dbname kalbt]
1. connect(dbname); CREATE TABLE IF NOT EXISTS schema_migration (same columns as above)
2. files = sorted db/migrations/*.sql; recorded = {id: checksum} from schema_migration
3. a recorded file whose checksum differs from the file now -> stop with an error (never edit an
   applied migration; write a new one)
4. pending = files not recorded; none -> print "nothing to apply", exit 0
5. if dbname == "kalbt": backup.dump(f"before-{first pending id}")
6. for each pending file, in its own transaction: execute the file, INSERT its id and checksum;
   print "applied <id>"
```

## Agreed tests (`tests/db/check_migrate.py`, qa-tester)
All on throwaway databases built from `db/schema.sql`; the live database is touched only by the
read-only count comparison in test 4.
1. A fresh build from `db/schema.sql` already records `0001_baseline` with the file's checksum, and
   `python -m db.migrate --dbname <throwaway>` prints "nothing to apply".
2. With a temporary extra migration file `9999_test.sql` (creating a small table) placed in a copy
   of the migrations folder (or via an option, the test's choice), the first run applies it once
   and records it, and a second run applies nothing.
3. A recorded checksum that does not match its file makes migrate stop with an error and apply
   nothing.
4. `dump` then `restore` into `kalbt_restore_test` gives, for every table, the same row count as
   the live database at the time of the dump; the throwaway database is dropped afterwards.
5. `restore(..., "kalbt")` is refused.
6. Rotation: with more than 10 dump files in a temporary backup directory, only the newest 10 remain.

## Real runs (implementer)
- `uv run python -m db.migrate` on the live database: takes a backup, applies `0001_baseline`.
  Report the backup file name.
- Run `tests.ingest.check_qa_kxhighny_dump` after the path change: still all PASS.

## Test files
- `tests/db/check_migrate.py`

## Lead's note (2026-11-24)
`tests/db/test_schema.sql` test 1 lists the exact tables; `schema_migration` was added to its
expected list (15 tables). The implementer also added a `conn.commit()` after reading the recorded
migrations: without it the per-file transaction was a savepoint and nothing was committed.

## Done when
- migrate on live recorded `0001_baseline` and a backup file exists
- `uv run python -m tests.db.check_migrate` and `uv run python -m tests.ingest.check_qa_kxhighny_dump` exit 0
- schema tests on a throwaway database still pass (63 / 48 / 15)

If anything here is unclear or looks wrong: stop and report, do not guess.
