"""QA gate for a weather dump: the same key must never carry two different values.

Run from the repo root:
  uv run python -m ingest.qa_weather_dump --station KNYC [--dump-dir PATH] [--markets PATH] [--db NAME]
The loader inserts with ON CONFLICT DO NOTHING, so a second, different value for an existing
key is dropped silently. This gate builds the rows the loader would insert and blocks when a key
carries two values, within the dump or between the dump and the database (SELECT only).
Writes _weather_qa_report.json into the dump folder, exits 0 on PASS and 1 on BLOCK.
"""
import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from db.connect import connect
from ingest.load_weather_dump import (
    MARKETS_FILE,
    WEATHER_DIR,
    cli_rows,
    forecast_rows,
    kalshi_rows,
    observation_rows,
)

# set name -> number of key columns (the table's primary key)
KEY_SIZES = {"nws_cli": 3, "kalshi": 3, "observation": 2, "forecast": 4}

OFFICIAL_DAILY_SELECT = """
    SELECT station_id, climate_date, source, high_f, high_time_lst, low_f, issued_at, reference
    FROM official_daily
    WHERE station_id = %(station)s AND source = %(source)s
      AND climate_date BETWEEN %(lo)s AND %(hi)s"""

OBSERVATION_SELECT = """
    SELECT station_id, observed_at, temp_f, dewpoint_f, rel_humidity_pct, wind_speed_kt,
           precip_in, metar
    FROM observation_hourly
    WHERE station_id = %(station)s AND observed_at BETWEEN %(lo)s AND %(hi)s"""

FORECAST_SELECT = """
    SELECT station_id, model, valid_time, lead_day, temp_f, dewpoint_f, cloud_cover_pct,
           wind_speed_mph, precip_in, shortwave_wm2
    FROM forecast_hourly
    WHERE station_id = %(station)s AND valid_time BETWEEN %(lo)s AND %(hi)s"""


def dump_row_sets(cur, station, dump_dir, markets_file):
    forecast = []
    for forecast_file in sorted(dump_dir.glob("forecast_*.json")):
        model = forecast_file.stem.removeprefix("forecast_")
        forecast += forecast_rows(station, model, forecast_file)
    return {
        "nws_cli": cli_rows(station, dump_dir),
        "kalshi": kalshi_rows(cur, markets_file),
        "observation": observation_rows(station, dump_dir),
        "forecast": forecast,
    }


def within_dump_conflicts(rows, n_key):
    """(number of exact duplicate rows, conflicts); a conflict is a key with two different values."""
    first = {}
    duplicates = 0
    conflicts = {}
    for row in rows:
        key, values = row[:n_key], row[n_key:]
        if key not in first:
            first[key] = values
        elif first[key] == values:
            duplicates += 1
        elif key not in conflicts:
            conflicts[key] = [first[key], values]
    examples = [{"key": list(key), "values": values} for key, values in conflicts.items()]
    return duplicates, examples


def db_comparison(cur, set_name, rows, n_key):
    """Compare the dump rows with the database rows inside the dump's key range."""
    if not rows:
        return {"new": 0, "equal": 0, "conflicts": []}

    station = rows[0][0]
    if set_name in ("nws_cli", "kalshi"):
        query = OFFICIAL_DAILY_SELECT
        range_column = 1
    elif set_name == "observation":
        query = OBSERVATION_SELECT
        range_column = 1
    else:
        query = FORECAST_SELECT
        range_column = 2
    params = {
        "station": station,
        "lo": min(row[range_column] for row in rows),
        "hi": max(row[range_column] for row in rows),
    }
    if set_name in ("nws_cli", "kalshi"):
        params["source"] = rows[0][2]
    cur.execute(query, params)
    db = {}
    for row in cur.fetchall():
        row = tuple(row)
        db[row[:n_key]] = row[n_key:]

    first = {}
    for row in rows:
        first.setdefault(row[:n_key], row[n_key:])

    new = 0
    equal = 0
    conflicts = []
    for key, values in first.items():
        if key not in db:
            new += 1
        elif db[key] == values:
            equal += 1
        else:
            conflicts.append({"key": list(key), "values": [values, db[key]]})
    return {"new": new, "equal": equal, "conflicts": conflicts}


def make_check(check_id, detail, conflicts):
    return {
        "id": check_id,
        "name": check_id.split("_", 1)[1],
        "status": "block" if conflicts else "pass",
        "detail": detail,
        "examples": conflicts[:5],
    }


def run_checks(conn, station, dump_dir, markets_file):
    with conn.cursor() as cur:
        row_sets = dump_row_sets(cur, station, dump_dir, markets_file)

        checks = []
        for set_name, rows in row_sets.items():
            duplicates, conflicts = within_dump_conflicts(rows, KEY_SIZES[set_name])
            detail = f"{len(rows)} rows, {duplicates} exact duplicates, {len(conflicts)} conflicting keys"
            checks.append(make_check(f"1_{set_name}_within", detail, conflicts))

        for set_name, rows in row_sets.items():
            result = db_comparison(cur, set_name, rows, KEY_SIZES[set_name])
            if rows:
                detail = (f"{len(rows)} rows: {result['new']} new, {result['equal']} equal, "
                          f"{len(result['conflicts'])} conflicting")
            else:
                detail = "0 rows"
            checks.append(make_check(f"2_{set_name}_db", detail, result["conflicts"]))

    blocked = any(check["status"] == "block" for check in checks)
    return {
        "station": station,
        "dump_dir": str(dump_dir),
        "markets_file": str(markets_file),
        "checked_at": datetime.now(UTC).isoformat(),
        "verdict": "BLOCK" if blocked else "PASS",
        "checks": checks,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--station", required=True)
    parser.add_argument("--dump-dir", type=Path)
    parser.add_argument("--markets", type=Path, default=MARKETS_FILE)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()
    dump_dir = args.dump_dir or WEATHER_DIR / args.station

    with connect(args.db) as conn:
        report = run_checks(conn, args.station, dump_dir, args.markets)

    for check in report["checks"]:
        tag = "PASS" if check["status"] == "pass" else "BLOCK"
        print(f"[{tag}] {check['id']} {check['name']}: {check['detail']}")
        for example in check["examples"]:
            print(f"        {example}")
    print(f"Verdict: {report['verdict']}")

    report_file = dump_dir / "_weather_qa_report.json"
    report_file.write_text(json.dumps(report, indent=2, default=str))
    sys.exit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
