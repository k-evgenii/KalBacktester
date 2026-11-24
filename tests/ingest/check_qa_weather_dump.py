"""Checks the weather dump QA gate (spec B018, agreed tests 1 to 10, plus extras).

Tests 1 to 8 and the extras run on a throwaway database kalbt_b018_test (db/schema.sql
applied, hand-made fixture, a fresh database per test, dropped at the end). Tests 9 and 10
only read the live database kalbt (SELECT).

Fixture: station KTST (offset -5), underlying U1, series KXHIGHTST.

Mini dump, one set of rows per climate day d (day of November 2026):
  nws_cli      one product, high d+40, low d+30, high_time "245 PM", issued d 06:21 UTC
  kalshi       one event KXHIGHTST-26NOVdd, expiration_value d+40, settled d 12:00 UTC
               (two markets, the second 5 minutes later; the loader takes the earliest)
  observation  d 00:51 UTC temp d+30, d 12:51 UTC temp d+40
  forecast     model m1, hours d 00:00 and d 12:00 UTC, leads 0 and 1 (leads 2 to 7 all null,
               so the loader skips them): 4 rows per day
So a dump of N days has N nws_cli, N kalshi, 2N observation and 4N forecast rows.
"""
import json
import re
import subprocess
import sys
import tempfile
import traceback
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect
from ingest.load_weather_dump import (
    OFFICIAL_DAILY_INSERT,
    cli_rows,
    forecast_rows,
    kalshi_rows,
    observation_rows,
)

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = REPO_ROOT / "db" / "schema.sql"
TEST_DB = "kalbt_b018_test"
LIVE_TABLES = ["official_daily", "observation_hourly", "forecast_hourly"]
CHECK_IDS = [
    "1_nws_cli_within", "1_kalshi_within", "1_observation_within", "1_forecast_within",
    "2_nws_cli_db", "2_kalshi_db", "2_observation_db", "2_forecast_db",
]
REAL_RUNS = [
    ("/home/kondr/KalBacktester/api_probe/weather/dump/KNYC",
     "/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY/markets.json"),
    ("/home/kondr/KalBacktester/api_probe/weather/dump_2026-06-01_2026-06-07/KNYC",
     "/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-06-01_2026-06-07/markets.json"),
    ("/home/kondr/KalBacktester/api_probe/weather/dump_2026-08-12_2026-08-18/KNYC",
     "/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-08-12_2026-08-18/markets.json"),
]

D = Decimal
UTC = timezone.utc
failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_test(name, func, *args):
    try:
        func(*args)
    except Exception:
        report(name, False, traceback.format_exc())
    else:
        report(name, True)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def check_equal(got, expected, what):
    if got != expected:
        raise AssertionError(f"{what}: got {got!r}, expected {expected!r}")


def utc(year, month, day, hour=0, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def norm(x):
    """Tuples and lists to lists, recursively, so examples compare with hand-written lists."""
    if isinstance(x, (tuple, list)):
        return [norm(i) for i in x]
    return x


# ---------------------------------------------------------------------------
# Mini dump writer
# ---------------------------------------------------------------------------

VARIABLES = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m",
             "precipitation", "shortwave_radiation"]


def write_dump(root, days):
    """Write a mini dump folder and its markets.json under root. Returns (dump_dir, markets_file)."""
    dump = root / "KTST"
    dump.mkdir(parents=True)

    cli = []
    for d in days:
        cli.append({"product": f"202611{d:02d}0621-KOKX-CDUS41-CLITST", "valid": f"2026-11-{d:02d}",
                    "high": d + 40, "high_time": "245 PM", "low": d + 30})
    (dump / "cli_2026.json").write_text(json.dumps(cli))

    lines = ["station,valid,tmpf,dwpf,relh,sknt,p01i,metar"]
    for d in days:
        lines.append(f"TST,2026-11-{d:02d} 00:51,{d + 30}.00,30.00,57.57,M,0.00,KTST {d:02d}0051Z AUTO")
        lines.append(f"TST,2026-11-{d:02d} 12:51,{d + 40}.00,30.00,57.57,6.00,0.00,KTST {d:02d}1251Z AUTO")
    (dump / "asos.csv").write_text("\n".join(lines) + "\n")

    times = []
    hourly = {name: [] for name in VARIABLES}
    for lead in range(1, 8):
        for name in VARIABLES:
            hourly[f"{name}_previous_day{lead}"] = []
    for d in days:
        for idx, hour in enumerate([0, 12]):
            times.append(f"2026-11-{d:02d}T{hour:02d}:00")
            lead0 = {"temperature_2m": d + 35 + idx, "dew_point_2m": 20, "cloud_cover": 50,
                     "wind_speed_10m": 5, "precipitation": 0, "shortwave_radiation": 100}
            lead1 = dict(lead0, temperature_2m=d + 34 + idx)
            for name in VARIABLES:
                hourly[name].append(lead0[name])
                hourly[f"{name}_previous_day1"].append(lead1[name])
                for lead in range(2, 8):
                    hourly[f"{name}_previous_day{lead}"].append(None)
    hourly["time"] = times
    (dump / "forecast_m1.json").write_text(json.dumps({"hourly": hourly}))

    markets = []
    for d in days:
        for minute in (0, 5):
            markets.append({"event_ticker": f"KXHIGHTST-26NOV{d:02d}", "expiration_value": str(d + 40),
                            "settlement_ts": f"2026-11-{d:02d}T12:{minute:02d}:00Z"})
    markets_file = root / "markets.json"
    markets_file.write_text(json.dumps({"markets": markets}))
    return dump, markets_file


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------

@contextmanager
def fresh_db():
    """A new kalbt_b018_test with the schema and the KTST fixture; yields an open connection."""
    admin = connect()
    admin.autocommit = True
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    conn = None
    try:
        conn = connect(dbname=TEST_DB)
        conn.execute(SCHEMA.read_text())
        conn.execute("INSERT INTO station (station_id, name, latitude, longitude, tz_name, "
                     "std_utc_offset_h, iem_id, iem_network) VALUES "
                     "('KTST', 'Test KTST', 40, -74, 'America/New_York', -5, 'TST', 'NY_ASOS')")
        conn.execute("INSERT INTO underlying (underlying_id, description, station_id) "
                     "VALUES ('U1', 'Test city', 'KTST')")
        conn.execute("INSERT INTO series (ticker, underlying_id, title) VALUES ('KXHIGHTST', 'U1', 'Test')")
        conn.commit()
        yield conn
    finally:
        if conn is not None:
            conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.close()


OBSERVATION_INSERT = (
    "INSERT INTO observation_hourly (station_id, observed_at, temp_f, dewpoint_f, "
    "rel_humidity_pct, wind_speed_kt, precip_in, metar) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)"
)
FORECAST_INSERT = (
    "INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f, "
    "dewpoint_f, cloud_cover_pct, wind_speed_mph, precip_in, shortwave_wm2) "
    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
)


def load_like_loader(conn, dump, markets_file):
    """INSERT exactly the rows the loader would insert (its row builders, ON CONFLICT DO NOTHING)."""
    with conn.cursor() as cur:
        cur.executemany(OFFICIAL_DAILY_INSERT, cli_rows("KTST", dump))
        cur.executemany(OFFICIAL_DAILY_INSERT, kalshi_rows(cur, markets_file))
        cur.executemany(OBSERVATION_INSERT + " ON CONFLICT DO NOTHING", observation_rows("KTST", dump))
        for forecast_file in sorted(dump.glob("forecast_*.json")):
            model = forecast_file.stem.removeprefix("forecast_")
            cur.executemany(FORECAST_INSERT + " ON CONFLICT DO NOTHING",
                            forecast_rows("KTST", model, forecast_file))
    conn.commit()


def run_gate(conn, dump, markets_file):
    from ingest.qa_weather_dump import run_checks
    return run_checks(conn, "KTST", dump, markets_file)


def run_cli(dump, markets_file, db):
    return subprocess.run(
        [sys.executable, "-m", "ingest.qa_weather_dump", "--station", "KTST" if db == TEST_DB else "KNYC",
         "--dump-dir", str(dump), "--markets", str(markets_file), "--db", db],
        cwd=REPO_ROOT, capture_output=True, text=True)


def by_id(rep, check_id):
    found = [c for c in rep["checks"] if c["id"] == check_id]
    check(len(found) == 1, f"check {check_id} not found exactly once")
    return found[0]


def expect_details(rep, expected):
    """expected: {check id: detail string}; every other check must pass."""
    check_equal([c["id"] for c in rep["checks"]], CHECK_IDS, "check ids in order")
    for c in rep["checks"]:
        if c["id"] in expected:
            check_equal(c["detail"], expected[c["id"]], f"detail of {c['id']}")


def expect_status(rep, blocked):
    """Only the check ids in `blocked` have status block; the verdict follows."""
    for c in rep["checks"]:
        want = "block" if c["id"] in blocked else "pass"
        check_equal(c["status"], want, f"status of {c['id']}")
    check_equal(rep["verdict"], "BLOCK" if blocked else "PASS", "verdict")


CLEAN_WITHIN = {
    "1_nws_cli_within": "3 rows, 0 exact duplicates, 0 conflicting keys",
    "1_kalshi_within": "3 rows, 0 exact duplicates, 0 conflicting keys",
    "1_observation_within": "6 rows, 0 exact duplicates, 0 conflicting keys",
    "1_forecast_within": "12 rows, 0 exact duplicates, 0 conflicting keys",
}


# ---------------------------------------------------------------------------
# Agreed tests 1 to 8
# ---------------------------------------------------------------------------

def test_01_clean_dump_empty_db(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    with fresh_db() as conn:
        rep = run_gate(conn, dump, markets)
    expected = dict(CLEAN_WITHIN)
    expected.update({
        "2_nws_cli_db": "3 rows: 3 new, 0 equal, 0 conflicting",
        "2_kalshi_db": "3 rows: 3 new, 0 equal, 0 conflicting",
        "2_observation_db": "6 rows: 6 new, 0 equal, 0 conflicting",
        "2_forecast_db": "12 rows: 12 new, 0 equal, 0 conflicting",
    })
    expect_details(rep, expected)
    expect_status(rep, [])
    for c in rep["checks"]:
        check_equal(c["examples"], [], f"examples of {c['id']}")


def test_02_nws_cli_within_conflict(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    cli_file = dump / "cli_2026.json"
    cli = json.loads(cli_file.read_text())
    cli.append({"product": "202611160721-KOKX-CDUS41-CLITST", "valid": "2026-11-16",
                "high": 99, "high_time": "245 PM", "low": 46})
    cli_file.write_text(json.dumps(cli))
    with fresh_db() as conn:
        rep = run_gate(conn, dump, markets)
    expect_details(rep, {"1_nws_cli_within": "4 rows, 0 exact duplicates, 1 conflicting keys"})
    expect_status(rep, ["1_nws_cli_within"])
    example = by_id(rep, "1_nws_cli_within")["examples"][0]
    check_equal(norm(example["key"]), ["KTST", date(2026, 11, 16), "nws_cli"], "example key")
    check_equal(norm(example["values"]), [
        [D("56"), "245 PM", D("46"), utc(2026, 11, 16, 6, 21), "202611160621-KOKX-CDUS41-CLITST"],
        [D("99"), "245 PM", D("46"), utc(2026, 11, 16, 7, 21), "202611160721-KOKX-CDUS41-CLITST"],
    ], "example values")


def test_02_kalshi_exits_with_loader_message(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    data = json.loads(markets.read_text())
    data["markets"][1]["expiration_value"] = "99"  # second market of 26NOV16 disagrees
    markets.write_text(json.dumps(data))
    with fresh_db():
        result = run_cli(dump, markets, TEST_DB)
    check(result.returncode != 0, f"exit code {result.returncode}, expected non-zero")
    text = result.stdout + result.stderr
    check("KXHIGHTST-26NOV16: markets carry different expiration_value" in text,
          f"loader message missing in output: {text!r}")


def test_02_observation_within_conflict(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    with open(dump / "asos.csv", "a") as f:
        f.write("TST,2026-11-16 00:51,99.00,30.00,57.57,M,0.00,KTST 160051Z AUTO\n")
    with fresh_db() as conn:
        rep = run_gate(conn, dump, markets)
    expect_details(rep, {"1_observation_within": "7 rows, 0 exact duplicates, 1 conflicting keys"})
    expect_status(rep, ["1_observation_within"])
    example = by_id(rep, "1_observation_within")["examples"][0]
    check_equal(norm(example["key"]), ["KTST", utc(2026, 11, 16, 0, 51)], "example key")
    check_equal(norm(example["values"]), [
        [D("46.00"), D("30.00"), D("57.57"), None, D("0.00"), "KTST 160051Z AUTO"],
        [D("99.00"), D("30.00"), D("57.57"), None, D("0.00"), "KTST 160051Z AUTO"],
    ], "example values")


def add_duplicate_forecast_hour(dump, change_temperature):
    """Append the 2026-11-16T00:00 hour a second time; optionally with a different lead-0 temperature."""
    forecast_file = dump / "forecast_m1.json"
    hourly = json.loads(forecast_file.read_text())["hourly"]
    for name, values in hourly.items():
        values.append(values[0])  # first hour in the file is 2026-11-16T00:00
    if change_temperature:
        hourly["temperature_2m"][-1] = 99
    forecast_file.write_text(json.dumps({"hourly": hourly}))


def test_02_forecast_within_conflict(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    add_duplicate_forecast_hour(dump, change_temperature=True)
    with fresh_db() as conn:
        rep = run_gate(conn, dump, markets)
    # lead 0 conflicts (99 against 51); lead 1 is an exact duplicate
    expect_details(rep, {"1_forecast_within": "14 rows, 1 exact duplicates, 1 conflicting keys"})
    expect_status(rep, ["1_forecast_within"])
    example = by_id(rep, "1_forecast_within")["examples"][0]
    check_equal(norm(example["key"]), ["KTST", "m1", utc(2026, 11, 16, 0, 0), 0], "example key")
    check_equal(norm(example["values"]), [
        [D("51"), D("20"), D("50"), D("5"), D("0"), D("100")],
        [D("99"), D("20"), D("50"), D("5"), D("0"), D("100")],
    ], "example values")


def test_03_exact_duplicates_pass(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    cli_file = dump / "cli_2026.json"
    cli = json.loads(cli_file.read_text())
    cli.append(cli[0])  # the same product twice
    cli_file.write_text(json.dumps(cli))
    with open(dump / "asos.csv", "a") as f:
        f.write("TST,2026-11-16 00:51,46.00,30.00,57.57,M,0.00,KTST 160051Z AUTO\n")
    add_duplicate_forecast_hour(dump, change_temperature=False)
    with fresh_db() as conn:
        rep = run_gate(conn, dump, markets)
    expect_details(rep, {
        "1_nws_cli_within": "4 rows, 1 exact duplicates, 0 conflicting keys",
        "1_kalshi_within": "3 rows, 0 exact duplicates, 0 conflicting keys",
        "1_observation_within": "7 rows, 1 exact duplicates, 0 conflicting keys",
        "1_forecast_within": "14 rows, 2 exact duplicates, 0 conflicting keys",
    })
    expect_status(rep, [])


def test_04_db_conflicts(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    with fresh_db() as conn:
        # one database row per set, differing from the dump row in exactly one column
        conn.execute(OFFICIAL_DAILY_INSERT, (
            "KTST", date(2026, 11, 16), "nws_cli", D("56"), "245 PM", D("46"),
            utc(2026, 11, 16, 7, 21), "202611160621-KOKX-CDUS41-CLITST"))  # issued_at +1 h
        conn.execute(OFFICIAL_DAILY_INSERT, (
            "KTST", date(2026, 11, 16), "kalshi", D("56"), None, None,
            utc(2026, 11, 16, 13, 0), "KXHIGHTST-26NOV16"))  # issued_at +1 h
        conn.execute(OBSERVATION_INSERT, (
            "KTST", utc(2026, 11, 16, 0, 51), D("47.00"), D("30.00"), D("57.57"), None, D("0.00"),
            "KTST 160051Z AUTO"))  # temp_f +1
        conn.execute(FORECAST_INSERT, (
            "KTST", "m1", utc(2026, 11, 16, 0, 0), 0, D("52"), D("20"), D("50"), D("5"), D("0"),
            D("100")))  # temp_f +1
        conn.commit()
        rep = run_gate(conn, dump, markets)
    expect_details(rep, dict(CLEAN_WITHIN, **{
        "2_nws_cli_db": "3 rows: 2 new, 0 equal, 1 conflicting",
        "2_kalshi_db": "3 rows: 2 new, 0 equal, 1 conflicting",
        "2_observation_db": "6 rows: 5 new, 0 equal, 1 conflicting",
        "2_forecast_db": "12 rows: 11 new, 0 equal, 1 conflicting",
    }))
    expect_status(rep, ["2_nws_cli_db", "2_kalshi_db", "2_observation_db", "2_forecast_db"])

    expected_examples = {
        "2_nws_cli_db": (
            ["KTST", date(2026, 11, 16), "nws_cli"],
            [[D("56"), "245 PM", D("46"), utc(2026, 11, 16, 6, 21), "202611160621-KOKX-CDUS41-CLITST"],
             [D("56"), "245 PM", D("46"), utc(2026, 11, 16, 7, 21), "202611160621-KOKX-CDUS41-CLITST"]]),
        "2_kalshi_db": (
            ["KTST", date(2026, 11, 16), "kalshi"],
            [[D("56"), None, None, utc(2026, 11, 16, 12, 0), "KXHIGHTST-26NOV16"],
             [D("56"), None, None, utc(2026, 11, 16, 13, 0), "KXHIGHTST-26NOV16"]]),
        "2_observation_db": (
            ["KTST", utc(2026, 11, 16, 0, 51)],
            [[D("46.00"), D("30.00"), D("57.57"), None, D("0.00"), "KTST 160051Z AUTO"],
             [D("47.00"), D("30.00"), D("57.57"), None, D("0.00"), "KTST 160051Z AUTO"]]),
        "2_forecast_db": (
            ["KTST", "m1", utc(2026, 11, 16, 0, 0), 0],
            [[D("51"), D("20"), D("50"), D("5"), D("0"), D("100")],
             [D("52"), D("20"), D("50"), D("5"), D("0"), D("100")]]),
    }
    for check_id, (key, values) in expected_examples.items():
        examples = by_id(rep, check_id)["examples"]
        check_equal(len(examples), 1, f"number of examples of {check_id}")
        check_equal(norm(examples[0]["key"]), key, f"key of {check_id}")
        check_equal(norm(examples[0]["values"]), values, f"values of {check_id} [dump, db]")


ALL_EQUAL_3_DAYS = {
    "2_nws_cli_db": "3 rows: 0 new, 3 equal, 0 conflicting",
    "2_kalshi_db": "3 rows: 0 new, 3 equal, 0 conflicting",
    "2_observation_db": "6 rows: 0 new, 6 equal, 0 conflicting",
    "2_forecast_db": "12 rows: 0 new, 12 equal, 0 conflicting",
}


def test_05_rerun_loaded_dump(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    with fresh_db() as conn:
        load_like_loader(conn, dump, markets)
        rep = run_gate(conn, dump, markets)
    expect_details(rep, dict(CLEAN_WITHIN, **ALL_EQUAL_3_DAYS))
    expect_status(rep, [])


def test_06_trailing_zero_equal(tmp):
    dump, markets = write_dump(tmp, [20])  # nws_cli and kalshi high is 60 in the dump
    with fresh_db() as conn:
        conn.execute(OFFICIAL_DAILY_INSERT, (
            "KTST", date(2026, 11, 20), "nws_cli", D("60.0"), "245 PM", D("50.0"),
            utc(2026, 11, 20, 6, 21), "202611200621-KOKX-CDUS41-CLITST"))
        conn.execute(OFFICIAL_DAILY_INSERT, (
            "KTST", date(2026, 11, 20), "kalshi", D("60.0"), None, None,
            utc(2026, 11, 20, 12, 0), "KXHIGHTST-26NOV20"))
        conn.execute(OBSERVATION_INSERT, (
            "KTST", utc(2026, 11, 20, 0, 51), D("50.0"), D("30.0"), D("57.570"), None, D("0.0"),
            "KTST 200051Z AUTO"))
        conn.execute(FORECAST_INSERT, (
            "KTST", "m1", utc(2026, 11, 20, 0, 0), 0, D("55.0"), D("20.0"), D("50.0"), D("5.0"),
            D("0.0"), D("100.0")))
        conn.commit()
        stored = conn.execute("SELECT high_f::text FROM official_daily WHERE source = 'nws_cli'").fetchone()[0]
        check_equal(stored, "60.0", "the database keeps the trailing zero")
        rep = run_gate(conn, dump, markets)
    expect_details(rep, {
        "2_nws_cli_db": "1 rows: 0 new, 1 equal, 0 conflicting",
        "2_kalshi_db": "1 rows: 0 new, 1 equal, 0 conflicting",
        "2_observation_db": "2 rows: 1 new, 1 equal, 0 conflicting",
        "2_forecast_db": "4 rows: 3 new, 1 equal, 0 conflicting",
    })
    expect_status(rep, [])


def test_07_overlapping_weeks(tmp):
    dump_a, markets_a = write_dump(tmp / "a", [16, 17, 18])
    dump_b, markets_b = write_dump(tmp / "b", [18, 19, 20])  # shares day 18
    with fresh_db() as conn:
        load_like_loader(conn, dump_a, markets_a)
        rep = run_gate(conn, dump_b, markets_b)
    expect_details(rep, dict(CLEAN_WITHIN, **{
        "2_nws_cli_db": "3 rows: 2 new, 1 equal, 0 conflicting",
        "2_kalshi_db": "3 rows: 2 new, 1 equal, 0 conflicting",
        "2_observation_db": "6 rows: 4 new, 2 equal, 0 conflicting",
        "2_forecast_db": "12 rows: 8 new, 4 equal, 0 conflicting",
    }))
    expect_status(rep, [])


def test_08_report_json_in_dump_folder(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    with fresh_db():
        result = run_cli(dump, markets, TEST_DB)
    check_equal(result.returncode, 0, f"exit code (stdout {result.stdout!r}, stderr {result.stderr!r})")
    report_file = dump / "_weather_qa_report.json"
    check(report_file.exists(), "report file _weather_qa_report.json not written to the dump folder")
    rep = json.loads(report_file.read_text())
    for key in ["station", "dump_dir", "markets_file", "checked_at", "verdict", "checks"]:
        check(key in rep, f"report key {key} missing")
    check_equal(rep["station"], "KTST", "station")
    check_equal(rep["verdict"], "PASS", "verdict")
    check_equal([c["id"] for c in rep["checks"]], CHECK_IDS, "check ids in order")
    for c in rep["checks"]:
        for key in ["id", "name", "status", "detail", "examples"]:
            check(key in c, f"check key {key} missing in {c['id']}")
    printed = [line for line in result.stdout.splitlines() if line.startswith("[PASS]")]
    check(len(printed) >= 8, f"expected at least 8 [PASS] lines in the output, got {result.stdout!r}")


# ---------------------------------------------------------------------------
# Extra tests
# ---------------------------------------------------------------------------

def test_11_extra_cli_block_exit_code(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    with open(dump / "asos.csv", "a") as f:
        f.write("TST,2026-11-16 00:51,99.00,30.00,57.57,M,0.00,KTST 160051Z AUTO\n")
    with fresh_db():
        result = run_cli(dump, markets, TEST_DB)
    check_equal(result.returncode, 1, "exit code on BLOCK")
    check("[BLOCK]" in result.stdout, f"no [BLOCK] line in output {result.stdout!r}")
    rep = json.loads((dump / "_weather_qa_report.json").read_text())
    check_equal(rep["verdict"], "BLOCK", "verdict in the report file")


def test_12_extra_forecast_file_per_model(tmp):
    dump, markets = write_dump(tmp, [16, 17, 18])
    (dump / "forecast_m2.json").write_text((dump / "forecast_m1.json").read_text())
    with fresh_db() as conn:
        rep = run_gate(conn, dump, markets)
    expect_details(rep, {"1_forecast_within": "24 rows, 0 exact duplicates, 0 conflicting keys",
                         "2_forecast_db": "24 rows: 24 new, 0 equal, 0 conflicting"})
    expect_status(rep, [])


def test_13_extra_db_range_only_overlap(tmp):
    """Database rows outside the dump's date range do not matter."""
    dump, markets = write_dump(tmp, [16, 17, 18])
    with fresh_db() as conn:
        conn.execute(OFFICIAL_DAILY_INSERT, (
            "KTST", date(2026, 11, 25), "nws_cli", D("1"), "245 PM", D("0"),
            utc(2026, 11, 25, 6, 21), "x"))
        conn.commit()
        rep = run_gate(conn, dump, markets)
    expect_details(rep, {"2_nws_cli_db": "3 rows: 3 new, 0 equal, 0 conflicting"})
    expect_status(rep, [])


# ---------------------------------------------------------------------------
# Tests 9 and 10: live database, read-only
# ---------------------------------------------------------------------------

def row_counts(conn):
    return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in LIVE_TABLES}


def test_10_real_dump(dump, markets):
    result = run_cli(dump, markets, "kalbt")
    check_equal(result.returncode, 0, f"exit code (stdout {result.stdout!r}, stderr {result.stderr!r})")
    rep = json.loads((Path(dump) / "_weather_qa_report.json").read_text())
    check_equal([c["id"] for c in rep["checks"]], CHECK_IDS, "check ids in order")
    for c in rep["checks"]:
        check_equal(c["status"], "pass", f"status of {c['id']}: {c['detail']} {c['examples']}")
        check_equal(c["examples"], [], f"examples of {c['id']}")
        if c["id"].startswith("1_"):
            found = re.fullmatch(r"(\d+) rows, \d+ exact duplicates, 0 conflicting keys", c["detail"])
            check(found, f"{c['id']} detail {c['detail']!r}")
        else:
            found = re.fullmatch(r"(\d+) rows: 0 new, (\d+) equal, 0 conflicting", c["detail"])
            check(found, f"{c['id']} detail {c['detail']!r}")
            check_equal(found.group(1), found.group(2), f"{c['id']} every row equal")
            check(int(found.group(1)) > 0, f"{c['id']} has no rows")
    check_equal(rep["verdict"], "PASS", "verdict")


def main():
    live_conn = connect()
    live_before = row_counts(live_conn)  # SELECT count(*) only
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            tmp = Path(tmp_name)
            tests = [
                ("01 clean dump, empty database: 8 pass, all rows new", test_01_clean_dump_empty_db),
                ("02a nws_cli within-dump conflict blocks 1_nws_cli_within", test_02_nws_cli_within_conflict),
                ("02b kalshi disagreement: gate exits non-zero with the loader's message",
                 test_02_kalshi_exits_with_loader_message),
                ("02c observation within-dump conflict blocks 1_observation_within",
                 test_02_observation_within_conflict),
                ("02d forecast within-dump conflict blocks 1_forecast_within", test_02_forecast_within_conflict),
                ("03 exact duplicates pass and are counted", test_03_exact_duplicates_pass),
                ("04 database conflicts block each 2_* check with [dump, db] examples", test_04_db_conflicts),
                ("05 re-run of a loaded dump: all equal, PASS", test_05_rerun_loaded_dump),
                ("06 60.0 in the database equals 60 in the dump", test_06_trailing_zero_equal),
                ("07 weeks overlapping by one day: shared rows equal, rest new", test_07_overlapping_weeks),
                ("08 report JSON written to the dump folder, eight checks in order",
                 test_08_report_json_in_dump_folder),
                ("11 (extra) CLI exits 1 and writes verdict BLOCK", test_11_extra_cli_block_exit_code),
                ("12 (extra) one forecast file per model, both counted", test_12_extra_forecast_file_per_model),
                ("13 (extra) database rows outside the dump's date range are ignored",
                 test_13_extra_db_range_only_overlap),
            ]
            for index, (name, func) in enumerate(tests):
                folder = tmp / f"t{index}"
                folder.mkdir()
                run_test(name, func, folder)

        for dump, markets in REAL_RUNS:
            run_test(f"10 real dump {Path(dump).parent.name}: PASS, all rows equal", test_10_real_dump,
                     dump, markets)

        def test_09():
            live_after = row_counts(live_conn)
            check_equal(live_after, live_before, "live row counts changed")
        run_test("09 live database row counts unchanged", test_09)
    finally:
        live_conn.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
