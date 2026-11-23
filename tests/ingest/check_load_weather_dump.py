"""Checks the loaded KNYC weather dump against the raw dump files (spec 009, tests 1 to 6)."""
import csv
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect

ROOT = Path(__file__).resolve().parent.parent.parent
WEATHER = ROOT / "api_probe" / "weather" / "dump" / "KNYC"
MARKETS = ROOT / "api_probe" / "kalshi" / "KXHIGHNY" / "markets.json"
STATION = "KNYC"
FORECAST_COLUMNS = {
    "temperature_2m": "temp_f",
    "dew_point_2m": "dewpoint_f",
    "cloud_cover": "cloud_cover_pct",
    "wind_speed_10m": "wind_speed_mph",
    "precipitation": "precip_in",
    "shortwave_radiation": "shortwave_wm2",
}
# Leads with data per model, spec 006 test 5
LEADS = {
    "ecmwf_ifs025": range(0, 8), "gfs_seamless": range(0, 8), "gem_seamless": range(0, 8),
    "jma_seamless": range(0, 8), "ncep_nbm_conus": range(0, 8),
    "icon_seamless": range(0, 7), "ukmo_seamless": range(0, 7),
    "meteofrance_seamless": range(0, 4), "gfs_hrrr": range(0, 2),
}

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def dec(value):
    return None if value is None else Decimal(str(value))


def utc(text, fmt):
    return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)


def load_json(path):
    with open(path) as f:
        return json.load(f, parse_float=Decimal)


def table_counts(cur):
    counts = {}
    cur.execute("SELECT source, count(*) FROM official_daily WHERE station_id = %s GROUP BY source",
                (STATION,))
    for source, n in cur.fetchall():
        counts["official_daily " + source] = n
    for table in ("observation_hourly", "forecast_hourly"):
        cur.execute(f"SELECT count(*) FROM {table} WHERE station_id = %s", (STATION,))
        counts[table] = cur.fetchone()[0]
    return counts


# ---------------------------------------------------------------- expectations from files
cli_rows = []
for path in sorted(WEATHER.glob("cli_*.json")):
    cli_rows.extend(load_json(path))

with open(WEATHER / "asos.csv", newline="") as f:
    asos_rows = list(csv.DictReader(f))

# (model, valid_time, lead) -> six values in the order of FORECAST_COLUMNS
forecast_expected = {}
model_files = sorted(WEATHER.glob("forecast_*.json"))
models_in_files = [p.name[len("forecast_"):-len(".json")] for p in model_files]
for path in model_files:
    model = path.name[len("forecast_"):-len(".json")]
    hourly = load_json(path)["hourly"]
    for i, t in enumerate(hourly["time"]):
        for lead in range(8):
            values = []
            for var in FORECAST_COLUMNS:
                col = var if lead == 0 else f"{var}_previous_day{lead}"
                values.append(dec(hourly[col][i]))
            if any(v is not None for v in values):
                forecast_expected[(model, utc(t, "%Y-%m-%dT%H:%M"), lead)] = tuple(values)

markets = load_json(MARKETS)["markets"]

conn = connect()
cur = conn.cursor()

# ---------------------------------------------------------------- test 1
before = table_counts(cur)
report("1a 7 nws_cli rows", before.get("official_daily nws_cli") == 7 == len(cli_rows),
       str(before))
report("1b 7 kalshi rows", before.get("official_daily kalshi") == 7, str(before))
report("1c observation_hourly rows equal asos.csv data rows",
       before["observation_hourly"] == len(asos_rows), f"{before['observation_hourly']} vs {len(asos_rows)}")
report("1d forecast_hourly rows equal non-null (model, time, lead) combinations",
       before["forecast_hourly"] == len(forecast_expected),
       f"{before['forecast_hourly']} vs {len(forecast_expected)}")

# ---------------------------------------------------------------- test 2
cur.execute("""SELECT climate_date, high_f, high_time_lst, low_f, reference, issued_at
               FROM official_daily WHERE station_id = %s AND source = 'nws_cli'""", (STATION,))
db_cli = {r[0]: r[1:] for r in cur.fetchall()}
exp_cli = {}
for row in cli_rows:
    day = datetime.strptime(row["valid"], "%Y-%m-%d").date()
    exp_cli[day] = (dec(row["high"]), row["high_time"], dec(row["low"]), row["product"],
                    utc(row["product"][:12], "%Y%m%d%H%M"))
report("2 every nws_cli row equals its CLI row", db_cli == exp_cli, f"{db_cli} vs {exp_cli}")

# ---------------------------------------------------------------- test 3
cur.execute("""SELECT observed_at, temp_f, dewpoint_f, rel_humidity_pct, wind_speed_kt, precip_in, metar
               FROM observation_hourly WHERE station_id = %s""", (STATION,))
db_obs = {r[0]: r[1:] for r in cur.fetchall()}


def missing_to_none(text):
    return None if text == "M" else Decimal(text)


exp_obs = {}
for row in asos_rows:
    exp_obs[utc(row["valid"], "%Y-%m-%d %H:%M")] = (
        missing_to_none(row["tmpf"]), missing_to_none(row["dwpf"]), missing_to_none(row["relh"]),
        missing_to_none(row["sknt"]), missing_to_none(row["p01i"]), row["metar"])
bad = [k for k in exp_obs if db_obs.get(k) != exp_obs[k]]
report("3a every observation row equals its CSV row", not bad and set(db_obs) == set(exp_obs),
       f"{len(bad)} differ, first: {[(k, db_obs.get(k), exp_obs[k]) for k in bad[:2]]}")
m_cells = sum(1 for row in asos_rows for col in ("tmpf", "dwpf", "relh", "sknt", "p01i") if row[col] == "M")
null_cells = sum(1 for v in db_obs.values() for x in v[:5] if x is None)
report("3b M cells are NULL (and nothing else is)", m_cells == null_cells and m_cells > 0,
       f"M cells {m_cells}, NULL cells {null_cells}")

# ---------------------------------------------------------------- test 4
cur.execute("""SELECT model, valid_time, lead_day, temp_f, dewpoint_f, cloud_cover_pct,
                      wind_speed_mph, precip_in, shortwave_wm2
               FROM forecast_hourly WHERE station_id = %s""", (STATION,))
db_fc = {(r[0], r[1], r[2]): tuple(r[3:]) for r in cur.fetchall()}
bad = [k for k in forecast_expected if db_fc.get(k) != forecast_expected[k]]
report("4a every forecast row equals its file value", not bad and set(db_fc) == set(forecast_expected),
       f"{len(bad)} differ, first: {[(k, db_fc.get(k), forecast_expected[k]) for k in bad[:2]]}")
report("4b ecmwf_aifs025 has no rows and the file exists",
       "ecmwf_aifs025" in models_in_files and not any(k[0] == "ecmwf_aifs025" for k in db_fc))
db_leads = {}
for model, _, lead in db_fc:
    db_leads.setdefault(model, set()).add(lead)
report("4c leads per model match spec 006 coverage",
       db_leads == {m: set(r) for m, r in LEADS.items()}, str(db_leads))
negatives = sum(1 for v in forecast_expected.values() for x in v[3:] if x is not None and x < 0)
db_negatives = sum(1 for v in db_fc.values() for x in v[3:] if x is not None and x < 0)
report("4d EXTRA negative wind/precip/radiation stored as returned",
       negatives == db_negatives, f"file {negatives}, db {db_negatives}")

# ---------------------------------------------------------------- test 5
cur.execute("""
    SELECT e.ticker, od.high_f, od.station_id
    FROM event e
    JOIN series s ON s.ticker = e.series_ticker
    JOIN underlying u ON u.underlying_id = s.underlying_id
    JOIN station st ON st.station_id = u.station_id
    JOIN official_daily od ON od.station_id = st.station_id
                          AND od.climate_date = e.event_date AND od.source = 'kalshi'
    ORDER BY e.ticker""")
linked = cur.fetchall()
inside = 0
for ticker, high, station_id in linked:
    cur.execute("""SELECT strike_type, floor_strike, cap_strike FROM market
                   WHERE event_ticker = %s AND result = 'yes'""", (ticker,))
    yes = cur.fetchall()
    if len(yes) != 1:
        print(f"  {ticker}: {len(yes)} yes markets")
        continue
    kind, floor, cap = yes[0]
    if kind == "greater":
        ok = high > floor
    elif kind == "less":
        ok = high < cap
    else:
        ok = floor <= high <= cap
    if ok:
        inside += 1
    else:
        print(f"  {ticker}: high {high} outside {kind} {floor} {cap}")
report("5a join links 7 events and 7 of 7 highs fall in the yes market's range",
       len(linked) == 7 and inside == 7, f"linked {len(linked)}, inside {inside}")
cur.execute("""SELECT k.climate_date, k.high_f, n.high_f
               FROM official_daily k JOIN official_daily n
                 ON n.station_id = k.station_id AND n.climate_date = k.climate_date AND n.source = 'nws_cli'
               WHERE k.source = 'kalshi' AND k.station_id = %s""", (STATION,))
pairs = cur.fetchall()
report("5b kalshi and nws_cli highs equal on all 7 days",
       len(pairs) == 7 and all(a == b for _, a, b in pairs), str(pairs))

# EXTRA kalshi rows against markets.json
events = {}
for m in markets:
    events.setdefault(m["event_ticker"], []).append(m)
exp_kalshi = {}
for et, ms in events.items():
    values = {dec(m["expiration_value"]) for m in ms}
    exp_kalshi[datetime.strptime(et.split("-", 1)[1], "%y%b%d").date()] = (
        values.pop() if len(values) == 1 else "DIFFERENT",
        min(datetime.fromisoformat(m["settlement_ts"].replace("Z", "+00:00")) for m in ms), et)
cur.execute("""SELECT climate_date, high_f, issued_at, reference, high_time_lst, low_f
               FROM official_daily WHERE station_id = %s AND source = 'kalshi'""", (STATION,))
rows = cur.fetchall()
db_kalshi = {r[0]: r[1:4] for r in rows}
report("EXTRA kalshi rows equal markets.json (high, earliest settlement, event ticker, NULL time/low)",
       db_kalshi == exp_kalshi and all(r[4] is None and r[5] is None for r in rows),
       f"{db_kalshi} vs {exp_kalshi}")

conn.rollback()
conn.close()

# ---------------------------------------------------------------- test 6
proc = subprocess.run([sys.executable, "-m", "ingest.load_weather_dump", "--station", STATION],
                      cwd=ROOT, capture_output=True, text=True)
print("--- loader rerun output ---")
print(proc.stdout.strip())
print(proc.stderr.strip())
print("---------------------------")
report("6a rerun exits 0", proc.returncode == 0, f"exit {proc.returncode}")
lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
nonzero = []
for ln in lines:
    numbers = [tok for tok in ln.replace(":", " ").replace("=", " ").replace(",", "").split() if tok.isdigit()]
    if any(int(n) != 0 for n in numbers):
        nonzero.append(ln)
report("6b rerun reports 0 rows inserted on every line",
       len(lines) >= 4 and not nonzero, f"{len(lines)} lines, nonzero: {nonzero}")
conn = connect()
cur = conn.cursor()
after = table_counts(cur)
conn.close()
report("6c counts unchanged after rerun", before == after, f"{before} vs {after}")

print(f"{len(failures)} failed")
sys.exit(1 if failures else 0)
