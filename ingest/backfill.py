"""Backfill runner: markets and weather for every seeded city, week by week.

Run from the repo root:
    uv run python -m ingest.backfill [--start 2026-08-24] [--end 2026-11-22] [--series S ...] [--dry]
Calls the existing dump and load scripts as subprocesses. City-weeks already in the
database are skipped, so the run can be stopped and restarted. A blocked or failed week
is logged to ingest/backfill_log.jsonl and the run continues.
"""
import argparse
import json
import subprocess
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from db.backup import dump
from db.connect import connect

REPO_ROOT = Path(__file__).resolve().parent.parent
LOG_FILE = REPO_ROOT / "ingest" / "backfill_log.jsonl"
STEP_TIMEOUT_SECONDS = 1800

CITIES_SQL = (
    "SELECT se.ticker, st.station_id, st.iem_network FROM series se "
    "JOIN underlying u USING (underlying_id) "
    "JOIN station st ON st.station_id = u.station_id ORDER BY se.ticker"
)
EVENTS_SQL = (
    "SELECT count(*) FROM event WHERE series_ticker = %s AND event_date BETWEEN %s AND %s"
)
OFFICIAL_SQL = (
    "SELECT count(*) FROM official_daily WHERE station_id = %s AND source = 'kalshi' "
    "AND climate_date BETWEEN %s AND %s"
)


def list_weeks(start, end):
    """(monday, sunday) pairs from start to end."""
    weeks = []
    monday = start
    while monday + timedelta(days=6) <= end:
        weeks.append((monday, monday + timedelta(days=6)))
        monday += timedelta(days=7)
    return weeks


def load_cities(series_filter):
    with connect() as conn:
        rows = conn.execute(CITIES_SQL).fetchall()
    if series_filter:
        rows = [row for row in rows if row[0] in series_filter]
    return rows


def is_done(conn, series, station, monday, sunday):
    events = conn.execute(EVENTS_SQL, (series, monday, sunday)).fetchone()[0]
    official = conn.execute(OFFICIAL_SQL, (station, monday, sunday)).fetchone()[0]
    return events == 7 and official == 7


def build_steps(series, station, network, monday, sunday):
    m_dir = REPO_ROOT / "api_probe" / "kalshi" / f"{series}_{monday}_{sunday}"
    w_dir = REPO_ROOT / "api_probe" / "weather" / f"dump_{monday}_{sunday}" / station
    return [
        ["uv", "run", "python", "api_probe/kalshi/dump_kxhighny.py", "--series", series,
         "--start", str(monday), "--end", str(sunday), "--no-trades", "--out", str(m_dir)],
        ["uv", "run", "python", "-m", "ingest.load_kxhighny_dump", str(m_dir)],
        ["uv", "run", "python", "api_probe/weather/dump_weather.py", "--station", station,
         "--network", network, "--start", str(monday), "--end", str(sunday), "--out", str(w_dir)],
        ["uv", "run", "python", "-m", "ingest.load_weather_dump", "--station", station,
         "--dump-dir", str(w_dir), "--markets", str(m_dir / "markets.json")],
    ]


def run_week(series, station, network, monday, sunday):
    """Run the four steps; return (status, failed_step, tail)."""
    steps = build_steps(series, station, network, monday, sunday)
    output = ""
    for index, command in enumerate(steps):
        try:
            result = subprocess.run(command, cwd=REPO_ROOT, capture_output=True, text=True,
                                    timeout=STEP_TIMEOUT_SECONDS)
            output = result.stdout + result.stderr
            failed = result.returncode != 0
        except subprocess.TimeoutExpired:
            output = f"timeout after {STEP_TIMEOUT_SECONDS} s"
            failed = True
        if failed:
            is_load_step = index in (1, 3)
            status = "blocked" if is_load_step and "BLOCK" in output else "error"
            return status, index, last_lines(output)
    return "loaded", None, last_lines(output)


def last_lines(text):
    return "\n".join(text.splitlines()[-15:])


def write_log(series, station, monday, sunday, status, failed_step, tail):
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "series": series, "station": station, "monday": str(monday), "sunday": str(sunday),
        "status": status, "failed_step": failed_step, "tail": tail,
    }
    with open(LOG_FILE, "a") as log:
        log.write(json.dumps(entry) + "\n")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2026-08-24")
    parser.add_argument("--end", default="2026-11-22")
    parser.add_argument("--series", nargs="+")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)
    if start.weekday() != 0:
        sys.exit(f"--start {start} is not a Monday")
    if end.weekday() != 6:
        sys.exit(f"--end {end} is not a Sunday")

    weeks = list_weeks(start, end)
    cities = load_cities(args.series)
    totals = Counter()

    with connect() as conn:
        for series, station, network in cities:
            todo = []
            for monday, sunday in weeks:
                if is_done(conn, series, station, monday, sunday):
                    totals["skip"] += 1
                    if args.dry:
                        print(f"{series} {monday}..{sunday} skip")
                else:
                    todo.append((monday, sunday))
                    if args.dry:
                        print(f"{series} {monday}..{sunday} todo")
            if args.dry:
                totals["todo"] += len(todo)
                continue
            if not todo:
                continue
            backup_file = dump(f"before-backfill-{series}")
            print(f"{series} backup {backup_file.name}")
            for monday, sunday in todo:
                status, failed_step, tail = run_week(series, station, network, monday, sunday)
                write_log(series, station, monday, sunday, status, failed_step, tail)
                print(f"{series} {monday}..{sunday} {status}")
                totals[status] += 1

    print("totals: " + ", ".join(f"{status} {count}" for status, count in sorted(totals.items())))


if __name__ == "__main__":
    main()
