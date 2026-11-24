"""Dump, rotate and restore the reconciliation database (container kalbt-recon-db)."""
import argparse
import subprocess
from datetime import datetime, timezone
from pathlib import Path

BACKUP_DIR = Path("/home/kondr/KalBacktester/backups/db")
KEEP = 10
CONTAINER = "kalbt-recon-db"


def dump(reason):
    """pg_dump -Fc of database kalbt into BACKUP_DIR; keep only the newest KEEP dumps."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    file = BACKUP_DIR / f"{stamp}_{reason}.dump"
    command = ["docker", "exec", CONTAINER, "sh", "-c", 'pg_dump -Fc -U "$POSTGRES_USER" kalbt']
    with open(file, "wb") as out:
        result = subprocess.run(command, stdout=out, stderr=subprocess.PIPE)
    if result.returncode != 0:
        file.unlink()
        raise RuntimeError(f"pg_dump failed: {result.stderr.decode()}")
    if file.stat().st_size == 0:
        file.unlink()
        raise RuntimeError("pg_dump wrote an empty file")
    for old in sorted(BACKUP_DIR.glob("*.dump"))[:-KEEP]:
        old.unlink()
    return file


def restore(path, dbname):
    """Drop and recreate dbname, then load the dump into it. Never the live database."""
    if dbname == "kalbt":
        raise ValueError("refusing to restore into the live database kalbt")
    docker = ["docker", "exec", CONTAINER, "sh", "-c"]
    subprocess.run(docker + [f'dropdb --if-exists -U "$POSTGRES_USER" {dbname}'], check=True)
    subprocess.run(docker + [f'createdb -U "$POSTGRES_USER" {dbname}'], check=True)
    restore_command = ["docker", "exec", "-i", CONTAINER, "sh", "-c",
                       f'pg_restore --no-owner -U "$POSTGRES_USER" -d {dbname}']
    with open(path, "rb") as dump_file:
        subprocess.run(restore_command, stdin=dump_file, check=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    dump_parser = sub.add_parser("dump")
    dump_parser.add_argument("--reason", required=True)
    restore_parser = sub.add_parser("restore")
    restore_parser.add_argument("file")
    restore_parser.add_argument("--dbname", required=True)
    args = parser.parse_args()
    if args.command == "dump":
        print(dump(args.reason))
    else:
        restore(args.file, args.dbname)
