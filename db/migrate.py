"""Apply the pending db/migrations/*.sql files once each, taking a backup first on kalbt."""
import argparse
import hashlib
import sys
from pathlib import Path

from db import backup
from db.connect import connect

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def checksum(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()


def main(dbname):
    conn = connect(dbname)
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migration (
               id         text        PRIMARY KEY,
               checksum   text        NOT NULL,
               applied_at timestamptz NOT NULL DEFAULT now()
           )"""
    )
    conn.commit()

    files = sorted(MIGRATIONS_DIR.glob("*.sql"))
    recorded = dict(conn.execute("SELECT id, checksum FROM schema_migration").fetchall())
    conn.commit()  # end the read transaction so each migration below gets its own

    for file in files:
        if file.stem in recorded and recorded[file.stem] != checksum(file):
            sys.exit(f"error: {file.name} changed after it was applied; write a new migration")

    pending = [file for file in files if file.stem not in recorded]
    if not pending:
        print("nothing to apply")
        return

    if dbname == "kalbt":
        backup.dump(f"before-{pending[0].stem}")

    for file in pending:
        with conn.transaction():
            conn.execute(file.read_text())
            conn.execute(
                "INSERT INTO schema_migration (id, checksum) VALUES (%s, %s)",
                (file.stem, checksum(file)),
            )
        print(f"applied {file.stem}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--dbname", default="kalbt")
    main(parser.parse_args().dbname)
