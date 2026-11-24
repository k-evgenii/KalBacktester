"""Checks the backtest harness (spec B017, agreed tests 1 to 8, plus a few extra).

Two throwaway databases, both dropped in a finally:
  kalbt_b017_test  restored from the newest dump in /home/kondr/KalBacktester/backups/db/
  kalbt_b017_fix   db/schema.sql plus a hand-made fixture
The live database kalbt is only read (row counts of the trading tables, before and after).
The CLI runs against the throwaway databases only, always with --db.

Fixture (all forecasts: five models of 56 F at lead 1, so the strategy picks bracket 56..57)
  station KTST (offset -5), underlying U1, series S1: events 2026-03-01 and 2026-03-02 are loaded
      (candles + kalshi official value); 2026-03-03 has no candles; 2026-03-05 has candles but no
      kalshi value. So S1 is loaded for 2026-03-01 .. 2026-03-02, 2 events.
  station KPAC (offset -8), underlying U2, series S2: one loaded event, 2026-03-01.
  underlying U4 (station KPAC), series S4: one event with candles but no kalshi value.
  underlying U3 (no station), series S3: one event with candles.
Each event has a ladder of six markets; B56 (56 to 57) settles yes, the others no.
"""
import json
import re
import subprocess
import sys
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from backtest.run_all import loaded_series, run_series
from db.connect import connect, read_password
from recon.check_run import check_run
from strategies.forecast_favourite import DEFAULTS, intents_for_series

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = REPO_ROOT / "db" / "schema.sql"
REPORTS = REPO_ROOT / "backtest" / "reports"
DUMPS = Path("/home/kondr/KalBacktester/backups/db")
TEST_DB = "kalbt_b017_test"
FIX_DB = "kalbt_b017_fix"
TRADING_TABLES = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]
ENGINE_DEFAULT_KEYS = ["fill_delay_minutes", "volume_window_minutes", "volume_share", "fee_rate",
                       "starting_cash"]

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


def row_counts(conn):
    counts = {}
    for table in TRADING_TABLES:
        counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def admin_connection():
    return psycopg.connect(host="127.0.0.1", port=5433, dbname="kalbt", user="kalbt",
                           password=read_password(), autocommit=True)


def restore_latest_dump():
    dump = sorted(DUMPS.glob("*.dump"))[-1]
    print(f"restoring {dump.name}")
    with open(dump, "rb") as f:
        result = subprocess.run(
            ["docker", "exec", "-i", "kalbt-recon-db", "sh", "-c",
             f'pg_restore --no-owner -U "$POSTGRES_USER" -d {TEST_DB}'],
            stdin=f, capture_output=True)
    stderr = result.stderr.decode(errors="replace")
    print(f"pg_restore exit code {result.returncode}")
    if stderr.strip():
        print("pg_restore printed:\n" + stderr[:2000])
    if result.returncode != 0:
        raise RuntimeError("pg_restore failed")


def cli(*args):
    """Run the harness CLI; returns the finished process (text output)."""
    return subprocess.run([sys.executable, "-m", "backtest.run_all", *args],
                          cwd=REPO_ROOT, capture_output=True, text=True)


def report_files():
    """Report files with their modification times. A file counts as new when it is missing from the
    earlier snapshot or has a newer mtime (two runs in the same second share a file name)."""
    if not REPORTS.exists():
        return {}
    return {p: p.stat().st_mtime_ns for p in REPORTS.glob("run_all_*.json")}


def newest_new_report(before):
    now = report_files()
    new = [p for p, mtime in now.items() if before.get(p) != mtime]
    check(len(new) == 1, f"expected exactly 1 new or rewritten report file, got {len(new)}")
    return json.loads(new[0].read_text())


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def utc(day, hour=0, minute=0):
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=hour, minutes=minute)


def add_event(cur, ticker, series, underlying, event_date, offset, candles):
    """One event with a six-market ladder; B56 settles yes. candles=True adds quotes before the
    09:00 LST decision (four candles of volume 100, so the engine can fill up to 40 contracts)."""
    cur.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                " VALUES (%s, %s, %s, %s, 'NWS')", (ticker, series, underlying, event_date))
    opens = utc(event_date) - timedelta(days=1)
    closes = utc(event_date) + timedelta(days=1)
    settled_at = utc(event_date, 36)  # D+1 12:00 UTC
    ladder = [("LT", "less", None, 52), ("B52", "between", 52, 53), ("B54", "between", 54, 55),
              ("B56", "between", 56, 57), ("B58", "between", 58, 59), ("GT", "greater", 59, None)]
    decided = utc(event_date, 9 - offset)
    for suffix, strike_type, floor_strike, cap_strike in ladder:
        market = f"{ticker}-{suffix}"
        result = "yes" if suffix == "B56" else "no"
        cur.execute("INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike,"
                    " open_time, close_time, result, settled_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                    (market, ticker, strike_type, floor_strike, cap_strike, opens, closes, result,
                     settled_at))
        if candles:
            for minutes_before in (45, 30, 15, 0):
                cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                            " VALUES (%s, %s, %s, %s, 100)",
                            (market, decided - timedelta(minutes=minutes_before), D("0.40"), D("0.45")))


def add_models(cur, station, offset, event_date):
    """Five models, hourly at lead 1, daily maximum exactly 56 (hour 12 holds 56, others 46)."""
    day_start = utc(event_date) - timedelta(hours=offset)
    for number in range(1, 6):
        for hour in range(24):
            cur.execute("INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f)"
                        " VALUES (%s, %s, %s, 1, %s)",
                        (station, f"m{number}", day_start + timedelta(hours=hour),
                         D(56) if hour == 12 else D(46)))


def add_official(cur, station, event_date, offset, with_kalshi):
    """nws_cli with the high at 3 PM LST (after the decision) and, if asked, the kalshi value 57."""
    issued = utc(event_date, 24 + 10)
    cur.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst,"
                " low_f, issued_at, reference) VALUES (%s, %s, 'nws_cli', 57, '300 PM', 40, %s, 'CLITST')",
                (station, event_date, issued))
    if with_kalshi:
        cur.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at,"
                    " reference) VALUES (%s, %s, 'kalshi', 57, %s, 'KX-TEST')",
                    (station, event_date, issued))


def load_fixture(conn):
    cur = conn.cursor()
    for station, offset, iem in (("KTST", -5, "TST"), ("KPAC", -8, "PAC")):
        cur.execute(
            "INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h,"
            " iem_id, iem_network) VALUES (%s, %s, 40, -74, 'America/New_York', %s, %s, 'NY_ASOS')",
            (station, f"Test {station}", offset, iem))
    cur.execute("INSERT INTO underlying (underlying_id, description, station_id) VALUES"
                " ('U1', 'City one', 'KTST'), ('U2', 'City two', 'KPAC'), ('U3', 'No station', NULL),"
                " ('U4', 'City four', 'KPAC')")
    cur.execute("INSERT INTO series (ticker, underlying_id, title) VALUES"
                " ('S1', 'U1', 'one'), ('S2', 'U2', 'two'), ('S3', 'U3', 'three'), ('S4', 'U4', 'four')")

    # S1: two loaded events, one without candles, one without a kalshi value
    for name, day in (("S1-D1", date(2026, 3, 1)), ("S1-D2", date(2026, 3, 2))):
        add_event(cur, name, "S1", "U1", day, -5, candles=True)
        add_models(cur, "KTST", -5, day)
        add_official(cur, "KTST", day, -5, with_kalshi=True)
    day = date(2026, 3, 3)
    add_event(cur, "S1-D3", "S1", "U1", day, -5, candles=False)
    add_models(cur, "KTST", -5, day)
    add_official(cur, "KTST", day, -5, with_kalshi=True)
    day = date(2026, 3, 5)
    add_event(cur, "S1-D5", "S1", "U1", day, -5, candles=True)
    add_models(cur, "KTST", -5, day)
    add_official(cur, "KTST", day, -5, with_kalshi=False)
    # S2: one loaded event
    day = date(2026, 3, 1)
    add_event(cur, "S2-D1", "S2", "U2", day, -8, candles=True)
    add_models(cur, "KPAC", -8, day)
    add_official(cur, "KPAC", day, -8, with_kalshi=True)
    # S4: station but markets with no kalshi value
    day = date(2026, 3, 8)
    add_event(cur, "S4-D8", "S4", "U4", day, -8, candles=True)
    add_models(cur, "KPAC", -8, day)
    add_official(cur, "KPAC", day, -8, with_kalshi=False)
    # S3: no station at all
    add_event(cur, "S3-D1", "S3", "U3", date(2026, 3, 1), -5, candles=True)
    conn.commit()


# ---------------------------------------------------------------------------
# Restored copy
# ---------------------------------------------------------------------------

LOADED_SQL = """
SELECT e.series_ticker, count(*), min(e.event_date), max(e.event_date)
FROM event e
JOIN underlying u ON u.underlying_id = e.underlying_id
WHERE EXISTS (SELECT 1 FROM market m JOIN candle c ON c.market_ticker = m.ticker
              WHERE m.event_ticker = e.ticker)
  AND EXISTS (SELECT 1 FROM official_daily o WHERE o.station_id = u.station_id
              AND o.climate_date = e.event_date AND o.source = 'kalshi')
GROUP BY e.series_ticker ORDER BY e.series_ticker
"""


def test_01_loaded_series_restored(conn):
    series = loaded_series(conn)
    expected = conn.execute(LOADED_SQL).fetchall()
    check([s["series_ticker"] for s in series] == [row[0] for row in expected],
          f"series {[s['series_ticker'] for s in series]} vs SQL {[r[0] for r in expected]}")
    by_ticker = {s["series_ticker"]: s for s in series}
    check("KXHIGHNY" in by_ticker, "KXHIGHNY not returned")
    for ticker, count, first, last in expected:
        s = by_ticker[ticker]
        check((s["n_events"], s["date_start"], s["date_end"]) == (count, first, last),
              f"{ticker}: got {s['n_events']} {s['date_start']} {s['date_end']}, SQL {count} {first} {last}")
    ny = by_ticker["KXHIGHNY"]
    check(set(ny) == {"series_ticker", "underlying_id", "station_id", "date_start", "date_end", "n_events"},
          f"keys {sorted(ny)}")
    check(ny["n_events"] >= 1, "KXHIGHNY has no events")
    print(f"       KXHIGHNY {ny['date_start']} to {ny['date_end']}, {ny['n_events']} events;"
          f" {len(series)} series loaded")


def test_02_dry(conn, series):
    before = row_counts(conn)
    reports_before = report_files()
    result = cli("--db", TEST_DB, "--dry")
    check(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-600:]}")
    check(row_counts(conn) == before, f"trading tables changed: {before} -> {row_counts(conn)}")
    results = newest_new_report(reports_before)
    check([r["series_ticker"] for r in results] == [s["series_ticker"] for s in series],
          f"report series {[r['series_ticker'] for r in results]}")
    for s, r in zip(series, results):
        intents, _ = intents_for_series(conn, s["series_ticker"], s["date_start"], s["date_end"],
                                        dict(DEFAULTS))
        check(r["n_intents"] == len(intents), f"{s['series_ticker']} report n_intents {r['n_intents']}"
              f" vs {len(intents)}")
        check(r["status"] in ("dry", "no_intents"), f"{s['series_ticker']} status {r['status']}")
        check(r["run_id"] is None, f"{s['series_ticker']} run_id {r['run_id']}")
        lines = [line for line in result.stdout.splitlines() if s["series_ticker"] in line.split()
                 or line.startswith(s["series_ticker"])]
        check(len(lines) >= 1, f"no output line for {s['series_ticker']}")
        check(any(str(len(intents)) in line.split() for line in lines),
              f"intent count {len(intents)} not in line: {lines}")


def new_run_rows(conn, ticker):
    return conn.execute(
        "SELECT run_id, strategy, params, status FROM run r WHERE strategy = 'forecast_favourite'"
        " AND EXISTS (SELECT 1 FROM order_intent i JOIN market m ON m.ticker = i.market_ticker"
        " JOIN event e ON e.ticker = m.event_ticker WHERE i.run_id = r.run_id AND e.series_ticker = %s)"
        " ORDER BY run_id", (ticker,)).fetchall()


def test_03_real_run(conn, series, state):
    before = row_counts(conn)
    reports_before = report_files()
    result = cli("--db", TEST_DB)
    print(result.stdout[-3000:])
    check(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-600:]}")
    results = newest_new_report(reports_before)
    state["first_results"] = results
    check([r["series_ticker"] for r in results] == [s["series_ticker"] for s in series],
          f"report series {[r['series_ticker'] for r in results]}")
    ran = [r for r in results if r["status"] == "ran"]
    check(any(r["series_ticker"] == "KXHIGHNY" for r in ran), "KXHIGHNY did not run")
    for r in results:
        check(r["status"] in ("ran", "no_intents"), f"{r['series_ticker']} status {r['status']}")
    after = row_counts(conn)
    check(after["run"] == before["run"] + len(ran), f"runs {before['run']} -> {after['run']}, ran {len(ran)}")
    strategy_params = dict(DEFAULTS)
    for r in ran:
        rows = new_run_rows(conn, r["series_ticker"])
        check(len(rows) == 1, f"{r['series_ticker']}: {len(rows)} runs")
        run_id, strategy, params, status = rows[0]
        check(run_id == r["run_id"], f"{r['series_ticker']} report run_id {r['run_id']} vs table {run_id}")
        check(strategy == "forecast_favourite" and status == "done", f"strategy {strategy} status {status}")
        check(r["recon_verdict"] == "PASS" and r["failed_checks"] == [],
              f"{r['series_ticker']} report verdict {r['recon_verdict']} {r['failed_checks']}")
        verdict = check_run(conn, run_id)["verdict"]
        check(verdict == "PASS", f"{r['series_ticker']} run {run_id} recheck verdict {verdict}")
        contained = conn.execute("SELECT %s::jsonb <@ params FROM run WHERE run_id = %s",
                                 (Jsonb(strategy_params), run_id)).fetchone()[0]
        check(contained, f"params {params} lack {strategy_params}")
        for key in ENGINE_DEFAULT_KEYS:
            check(key in params, f"params {params} lack engine key {key}")
        check(params["quantity"] == 10 and params["decision_time_lst"] == "09:00"
              and params["lead_day"] == 1 and params["min_models"] == 5, f"params {params}")
    state["ran"] = ran


def test_04_rerun_writes_nothing(conn, series, state):
    before = row_counts(conn)
    reports_before = report_files()
    result = cli("--db", TEST_DB)
    check(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-600:]}")
    check(row_counts(conn) == before, f"tables changed: {before} -> {row_counts(conn)}")
    results = newest_new_report(reports_before)
    first = {r["series_ticker"]: r for r in state["first_results"]}
    for r in results:
        old = first[r["series_ticker"]]
        if old["status"] == "ran":
            check(r["status"] == "already_done", f"{r['series_ticker']} status {r['status']}")
            check(r["run_id"] == old["run_id"], f"{r['series_ticker']} run_id {r['run_id']} vs {old['run_id']}")
        else:
            check(r["status"] == "no_intents", f"{r['series_ticker']} status {r['status']}")


def test_05_changed_param(conn, series, state):
    ny = [s for s in series if s["series_ticker"] == "KXHIGHNY"][0]
    before = row_counts(conn)
    params = dict(DEFAULTS, quantity=5)
    result = run_series(conn, ny, params, False)
    check(result["status"] == "ran", f"status {result['status']}")
    old_id = [r for r in state["ran"] if r["series_ticker"] == "KXHIGHNY"][0]["run_id"]
    check(result["run_id"] is not None and result["run_id"] != old_id, f"run_id {result['run_id']}")
    check(row_counts(conn)["run"] == before["run"] + 1, "expected exactly one new run")
    check(result["recon_verdict"] == "PASS", f"verdict {result['recon_verdict']}")
    quantity = conn.execute("SELECT params->>'quantity' FROM run WHERE run_id = %s",
                            (result["run_id"],)).fetchone()[0]
    check(quantity == "5", f"stored quantity {quantity}")
    # the same quantity-5 call again is now a repeat
    again = run_series(conn, ny, params, False)
    check(again["status"] == "already_done" and again["run_id"] == result["run_id"],
          f"repeat gave {again['status']} {again['run_id']}")


# ---------------------------------------------------------------------------
# Fixture database
# ---------------------------------------------------------------------------

def test_01b_fixture_loaded_series(conn):
    series = loaded_series(conn)
    tickers = [s["series_ticker"] for s in series]
    check(tickers == ["S1", "S2"], f"series {tickers} (S3 has no station, S4 no kalshi value)")
    s1, s2 = series
    check(s1 == {"series_ticker": "S1", "underlying_id": "U1", "station_id": "KTST",
                 "date_start": date(2026, 3, 1), "date_end": date(2026, 3, 2), "n_events": 2},
          f"S1 {s1}")
    check(s2 == {"series_ticker": "S2", "underlying_id": "U2", "station_id": "KPAC",
                 "date_start": date(2026, 3, 1), "date_end": date(2026, 3, 1), "n_events": 1},
          f"S2 {s2}")


def test_06_two_city_fixture(conn):
    before = row_counts(conn)
    series = loaded_series(conn)
    for s in series:
        result = run_series(conn, s, dict(DEFAULTS), False)
        check(result["status"] == "ran", f"{s['series_ticker']} status {result['status']}")
        check(result["n_intents"] == s["n_events"], f"{s['series_ticker']} intents {result['n_intents']}")
        check(result["recon_verdict"] == "PASS" and result["failed_checks"] == [],
              f"{s['series_ticker']} {result['recon_verdict']} {result['failed_checks']}")
        check(result["series_ticker"] == s["series_ticker"], "series_ticker in result")
        check(set(result["outcomes"]) == {"filled", "rejected", "blocked"}, f"outcomes {result['outcomes']}")
        check(result["outcomes"]["filled"] == s["n_events"], f"outcomes {result['outcomes']}")
        check(result["settlements"] == s["n_events"], f"settlements {result['settlements']}")
    check(row_counts(conn)["run"] == before["run"] + 2, "expected two new runs")
    # city with no station, hand-built series dict
    run_count = row_counts(conn)["run"]
    s3 = {"series_ticker": "S3", "underlying_id": "U3", "station_id": None,
          "date_start": date(2026, 3, 1), "date_end": date(2026, 3, 1), "n_events": 1}
    result = run_series(conn, s3, dict(DEFAULTS), False)
    check(result["status"] == "no_intents", f"S3 status {result['status']}")
    check(result["skipped_days"] == {"no_station": 1}, f"S3 skipped {result['skipped_days']}")
    check(result["n_intents"] == 0 and result["run_id"] is None, f"S3 {result}")
    check(result["recon_verdict"] is None and result["failed_checks"] == [], f"S3 {result}")
    check(row_counts(conn)["run"] == run_count, "S3 wrote a run row")


def test_09_fixture_dry_and_repeat(conn):
    """(extra) a dry call on the fixture writes nothing; S1's first run is then repeated."""
    s1 = [s for s in loaded_series(conn) if s["series_ticker"] == "S1"][0]
    before = row_counts(conn)
    result = run_series(conn, s1, dict(DEFAULTS, quantity=3), True)
    check(result["status"] == "dry" and result["run_id"] is None, f"dry gave {result['status']}")
    check(result["n_intents"] == 2, f"n_intents {result['n_intents']}")
    check(row_counts(conn) == before, "dry run wrote rows")
    existing = run_series(conn, s1, dict(DEFAULTS), False)
    first_id = conn.execute("SELECT min(run_id) FROM run").fetchone()[0]
    check(existing["status"] == "already_done", f"status {existing['status']}")
    check(existing["run_id"] == first_id, f"run_id {existing['run_id']} vs first {first_id}")
    check(row_counts(conn) == before, "already_done wrote rows")


def test_10_skipped_days(conn):
    """(extra) skipped_days counts reasons that occurred: S1 has no events beyond the loaded two, but
    a series dict that widens the range to include D3 and D5 still gets intents and a count."""
    s1 = {"series_ticker": "S1", "underlying_id": "U1", "station_id": "KTST",
          "date_start": date(2026, 3, 1), "date_end": date(2026, 3, 5), "n_events": 4}
    result = run_series(conn, s1, dict(DEFAULTS, quantity=4), True)
    check(result["status"] == "dry", f"status {result['status']}")
    check(result["n_intents"] == 4, f"n_intents {result['n_intents']}")
    check(result["skipped_days"] == {}, f"skipped_days {result['skipped_days']}")
    wide = run_series(conn, s1, dict(DEFAULTS, min_models=6, quantity=4), True)
    check(wide["status"] == "no_intents", f"status {wide['status']}")
    check(wide["skipped_days"] == {"too_few_models": 4}, f"skipped_days {wide['skipped_days']}")


# ---------------------------------------------------------------------------
# Test 7: missing --db
# ---------------------------------------------------------------------------

def test_07_missing_db(conn):
    before = row_counts(conn)
    reports_before = report_files()
    for args in ([], ["--dry"]):
        result = cli(*args)
        check(result.returncode != 0, f"exit {result.returncode} for {args}")
        check("--db" in result.stderr and "required" in result.stderr,
              f"stderr does not show the argparse error: {result.stderr[-400:]}")
    check(row_counts(conn) == before, "tables changed")
    check(report_files() == reports_before, "a report file was written")


# ---------------------------------------------------------------------------
# Test 11: report names carry microseconds
# ---------------------------------------------------------------------------

def test_11_unique_report_names():
    before = set(report_files())
    for _ in range(2):
        result = cli("--db", TEST_DB, "--dry")
        check(result.returncode == 0, f"exit {result.returncode}: {result.stderr[-600:]}")
    new = sorted(set(report_files()) - before)
    check(len(new) == 2, f"expected 2 new report files, got {[p.name for p in new]}")
    pattern = re.compile(r"run_all_\d{8}T\d{6}\.\d{6}Z\.json")
    for path in new:
        check(path.exists(), f"{path.name} missing")
        check(pattern.fullmatch(path.name), f"name {path.name} does not match {pattern.pattern}")


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    with connect() as live:
        live_before = row_counts(live)  # SELECT count(*) only
    admin = admin_connection()
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
    admin.execute(f"DROP DATABASE IF EXISTS {FIX_DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    admin.execute(f"CREATE DATABASE {FIX_DB}")
    try:
        restore_latest_dump()
        state = {}
        with connect(dbname=TEST_DB) as conn:
            run_test("01 loaded_series on the restored copy matches direct SQL, KXHIGHNY present",
                     test_01_loaded_series_restored, conn)
            series = loaded_series(conn)
            run_test("02 --dry writes nothing and lists each series with its intent count",
                     test_02_dry, conn, series)
            run_test("03 real run: one run per series, PASS, strategy and params stored",
                     test_03_real_run, conn, series, state)
            if "first_results" in state:
                run_test("04 rerun writes nothing and reports already_done with the first run ids",
                         test_04_rerun_writes_nothing, conn, series, state)
            else:
                report("04 rerun writes nothing and reports already_done", False, "test 03 gave no results")
            if "ran" in state:
                run_test("05 changed strategy param (quantity 5) makes a new run",
                         test_05_changed_param, conn, series, state)
            else:
                report("05 changed strategy param makes a new run", False, "test 03 gave no runs")
            run_test("07 --db missing: argparse error, nothing written", test_07_missing_db, conn)
            run_test("11 two back-to-back --dry calls write two report files with microsecond names",
                     test_11_unique_report_names)

        with connect(dbname=FIX_DB) as fix:
            fix.execute(SCHEMA.read_text())
            fix.commit()
            load_fixture(fix)
            run_test("01b fixture: loaded_series skips series without a kalshi value or a station",
                     test_01b_fixture_loaded_series, fix)
            run_test("06 two-city fixture: both series run PASS; city without station gives no_intents",
                     test_06_two_city_fixture, fix)
            run_test("09 (extra) dry call writes nothing; same params again is already_done",
                     test_09_fixture_dry_and_repeat, fix)
            run_test("10 (extra) skipped_days counts the reasons that occurred",
                     test_10_skipped_days, fix)
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB} WITH (FORCE)")
        admin.execute(f"DROP DATABASE IF EXISTS {FIX_DB} WITH (FORCE)")
        admin.close()
    with connect() as live:
        live_after = row_counts(live)
    report("08 live database trading tables unchanged", live_before == live_after,
           f"before {live_before}, after {live_after}")
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
