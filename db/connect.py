"""Connection to the reconciliation database (container kalbt-recon-db)."""
import os
from pathlib import Path

import psycopg


def read_password():
    """POSTGRES_PASSWORD from the environment, else from the repo's .env file."""
    if os.environ.get("POSTGRES_PASSWORD"):
        return os.environ["POSTGRES_PASSWORD"]
    env_file = Path(__file__).resolve().parent.parent / ".env"
    for line in env_file.read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POSTGRES_PASSWORD not found in the environment or .env")


def connect():
    return psycopg.connect(
        host="127.0.0.1", port=5433, dbname="kalbt", user="kalbt", password=read_password()
    )
