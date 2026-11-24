"""Checks spec 016: numeric station check and the three loaded New York weeks."""
import json
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect
from ingest.load_weather_dump import numeric_verdict

ROOT = Path(__file__).resolve().parent.parent.parent
STATION = "KNYC"
# (label, weather dump folder, markets file, first day); November uses the loader defaults
WEEKS = [
    ("June", "dump_2026-06-01_2026-06-07", "KXHIGHNY_2026-06-01_2026-06-07", date(2026, 6, 1)),
    ("August", "dump_2026-08-12_2026-08-18", "KXHIGHNY_2026-08-12_2026-08-18", date(2026, 8, 12)),
    ("November", "dump", "KXHIGHNY", date(2026, 11, 16)),
]
LST = timezone(timedelta(hours=-5))

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def weather_dir(folder):
    return ROOT / "api_probe" / "weather" / folder / STATION


def markets_file(folder):
    return ROOT / "api_probe" / "kalshi" / folder / "markets.json"


def kalshi_by_day(path):
    """event day -> expiration_value, only when all markets of the event agree."""
    with open(path) as f:
        markets = json.load(f, parse_float=Decimal)["markets"]
    events = {}
    for m in markets:
        events.setdefault(m["event_ticker"], set()).add(Decimal(str(m["expiration_value"])))
    days = {}
    for ticker, values in events.items():
        if len(values) == 1:
            days[datetime.strptime(ticker.split("-", 1)[1], "%y%b%d").date()] = values.pop()
    return days


def nws_by_day(folder):
    rows = []
    for path in sorted(weather_dir(folder).glob("cli_*.json")):
        with open(path) as f:
            rows.extend(json.load(f, parse_float=Decimal))
    return {datetime.strptime(r["valid"], "%Y-%m-%d").date(): Decimal(str(r["high"])) for r in rows}


def run_loader(*args):
    return subprocess.run([sys.executable, "-m", "ingest.load_weather_dump", "--station", STATION, *args],
                          cwd=ROOT, capture_output=True, text=True)


def all_table_counts(cur):
    cur.execute("SELECT table_name FROM information_schema.tables "
                "WHERE table_schema = 'public' AND table_type = 'BASE TABLE' ORDER BY table_name")
    counts = {}
    for (table,) in cur.fetchall():
        cur.execute(f'SELECT count(*) FROM "{table}"')
        counts[table] = cur.fetchone()[0]
    return counts


# ---------------------------------------------------------------- test 1
cases = [((7, 0), "ok"), ((7, 1), "warn"), ((7, 2), "block"), ((365, 7), "warn"),
         ((365, 8), "block"), ((100, 2), "warn"), ((100, 3), "block")]
for (n_days, n_bad), expected in cases:
    got = numeric_verdict(n_days, n_bad)
    report(f"1 numeric_verdict({n_days}, {n_bad}) == {expected}", got == expected, f"got {got}")
report("1 EXTRA numeric_verdict(0, 0) == ok", numeric_verdict(0, 0) == "ok")
report("1 EXTRA numeric_verdict(365, 0) == ok", numeric_verdict(365, 0) == "ok")

# ---------------------------------------------------------------- test 2
for label, weather_folder, markets_folder, first_day in WEEKS:
    kalshi = kalshi_by_day(markets_file(markets_folder))
    nws = nws_by_day(weather_folder)
    common = sorted(set(kalshi) & set(nws))
    expected_days = [first_day + timedelta(days=i) for i in range(7)]
    report(f"2 {label}: 7 days compared", common == expected_days, str(common))
    differ = [(d, kalshi[d], nws[d]) for d in common if kalshi[d] != nws[d]]
    report(f"2 {label}: Kalshi value equals NWS high on every day", not differ, str(differ))
    if label == "November":
        proc = run_loader()
    else:
        proc = run_loader("--dump-dir", str(weather_dir(weather_folder)),
                          "--markets", str(markets_file(markets_folder)))
    report(f"2 {label}: loader exits 0", proc.returncode == 0, proc.stdout + proc.stderr)
    report(f"2 {label}: loader prints '7 days compared, 0 mismatches -> ok'",
           "7 days compared, 0 mismatches -> ok" in proc.stdout, proc.stdout)
    report(f"2 {label}: EXTRA rerun inserts 0 rows",
           len(re.findall(r"^\S.*: 0 rows inserted$", proc.stdout, re.M)) == 4, proc.stdout)

# ---------------------------------------------------------------- test 3
conn = connect()
cur = conn.cursor()
before = all_table_counts(cur)
conn.rollback()
conn.close()

scratch = Path(tempfile.mkdtemp(prefix="tampered_weather_"))
copy = scratch / STATION
shutil.copytree(weather_dir("dump"), copy)
cli_path = next(copy.glob("cli_*.json"))
with open(cli_path) as f:
    cli_rows = json.load(f)
tampered_days = [cli_rows[1]["valid"], cli_rows[4]["valid"]]
for index in (1, 4):
    cli_rows[index]["high"] = cli_rows[index]["high"] + 7
with open(cli_path, "w") as f:
    json.dump(cli_rows, f)

proc = run_loader("--dump-dir", str(copy))
print("--- tampered run output ---")
print(proc.stdout.strip())
print(proc.stderr.strip()[-500:])
print("---------------------------")
report("3a tampered copy: loader exits 1", proc.returncode == 1, f"exit {proc.returncode}")
report("3b tampered copy: BLOCK line printed",
       "station check: BLOCK, nothing inserted" in proc.stdout, proc.stdout)
report("3c tampered copy: summary says 7 days, 2 mismatches, block",
       "station check: 7 days compared, 2 mismatches -> block" in proc.stdout, proc.stdout)
warn_lines = [ln for ln in proc.stdout.splitlines() if ln.startswith("WARN station check:")]
report("3d tampered copy: one WARN line per tampered day",
       len(warn_lines) == 2 and all(any(day in ln for ln in warn_lines) for day in tampered_days),
       str(warn_lines))
conn = connect()
cur = conn.cursor()
after = all_table_counts(cur)
conn.rollback()
conn.close()
report("3e tampered copy: no table's row count changed", before == after,
       str({t: (before[t], after.get(t)) for t in before if before[t] != after.get(t)}))
shutil.rmtree(scratch)

# ---------------------------------------------------------------- tests 4 and 5
all_days = []
for _, _, _, first_day in WEEKS:
    all_days.extend(first_day + timedelta(days=i) for i in range(7))

conn = connect()
cur = conn.cursor()
cur.execute("""SELECT source, climate_date, high_f FROM official_daily
               WHERE station_id = %s AND source IN ('nws_cli', 'kalshi')""", (STATION,))
official = {}
for source, day, high in cur.fetchall():
    official.setdefault(source, {})[day] = high
for source in ("nws_cli", "kalshi"):
    report(f"4a official_daily has 21 {source} rows, one per event day",
           sorted(official.get(source, {})) == sorted(all_days),
           f"{len(official.get(source, {}))} rows")
report("4b kalshi and nws_cli highs equal on every day",
       official.get("kalshi") == official.get("nws_cli"), str(official))

cur.execute("""SELECT e.event_date, e.ticker FROM event e
               JOIN series s ON s.ticker = e.series_ticker
               JOIN underlying u ON u.underlying_id = s.underlying_id
               WHERE u.station_id = %s AND e.event_date = ANY(%s) ORDER BY e.event_date""",
            (STATION, all_days))
events = cur.fetchall()
report("4c 21 events are linked to the station", [d for d, _ in events] == sorted(all_days),
       f"{len(events)} events")
outside = []
for day, ticker in events:
    cur.execute("""SELECT strike_type, floor_strike, cap_strike FROM market
                   WHERE event_ticker = %s AND result = 'yes'""", (ticker,))
    yes = cur.fetchall()
    high = official.get("kalshi", {}).get(day)
    if len(yes) != 1 or high is None:
        outside.append((ticker, f"{len(yes)} yes markets, high {high}"))
        continue
    kind, floor, cap = yes[0]
    if kind == "greater":
        ok = high > floor
    elif kind == "less":
        ok = high < cap
    else:
        ok = floor <= high <= cap
    if not ok:
        outside.append((ticker, high, kind, floor, cap))
report("4d official high falls in the winning market's strike range on all 21 events",
       not outside, str(outside))

# observation_hourly and forecast_hourly: climate day = local standard time (UTC-5) calendar day
cur.execute("SELECT DISTINCT model FROM forecast_hourly WHERE station_id = %s", (STATION,))
models = [r[0] for r in cur.fetchall()]
report("5 EXTRA forecast_hourly has models", len(models) > 0)
short_obs = []
missing_models = []
for day in all_days:
    start = datetime(day.year, day.month, day.day, tzinfo=LST)
    end = start + timedelta(days=1)
    cur.execute("""SELECT count(*) FROM observation_hourly
                   WHERE station_id = %s AND observed_at >= %s AND observed_at < %s""",
                (STATION, start, end))
    n_obs = cur.fetchone()[0]
    if n_obs < 24:
        short_obs.append((day, n_obs))
    cur.execute("""SELECT DISTINCT model FROM forecast_hourly
                   WHERE station_id = %s AND valid_time >= %s AND valid_time < %s""",
                (STATION, start, end))
    present = {r[0] for r in cur.fetchall()}
    if present != set(models):
        missing_models.append((day, sorted(set(models) - present)))
report("5a observation_hourly has at least 24 rows on each of 21 days", not short_obs, str(short_obs))
report("5b every model with data has forecast rows on each of 21 days", not missing_models,
       str(missing_models))
conn.rollback()
conn.close()

print(f"{len(failures)} failed")
sys.exit(1 if failures else 0)
