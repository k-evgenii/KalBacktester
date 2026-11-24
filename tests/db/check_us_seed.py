"""Checks the US daily-high seed migration (spec A019, agreed tests 1 to 6, plus extras).

Run from the repo root: uv run python -m tests.db.check_us_seed
Network: read-only calls to IEM (<= 1 req/s) and Kalshi (<= 2 req/s). Test 1 reruns the generator
(about 170 Kalshi requests) and restores the migration file if the output differs.
Test 5 uses the throwaway database kalbt_seed_test, dropped at the end.
The live database kalbt is only read.
"""
import json
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from db import backup
from db.connect import connect

ROOT = Path(__file__).resolve().parent.parent.parent
MIGRATION = ROOT / "db" / "migrations" / "0002_us_daily_high_stations.sql"
SCHEMA = ROOT / "db" / "schema.sql"
SCHEMA_TESTS = ROOT / "tests" / "db" / "test_schema.sql"
GENERATOR = "api_probe/kalshi/build_us_seed.py"
TEST_DB = "kalbt_seed_test"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
IEM = "https://mesonet.agron.iastate.edu/geojson/network.py"
WINDOW = (date(2026, 8, 24), date(2026, 11, 22))
CODE = re.compile(r"\(CLI([A-Z]{3})\)")
NYC_RULES_NAMES = ["(CLINYC)", "Central Park, New York", "New York City"]

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + str(detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


# ---------------------------------------------------------------- parse the migration file
def unquote(text):
    return text.replace("''", "'")


def parse_migration(path):
    sql = Path(path).read_text()
    text = r"((?:[^']|'')*)"
    stations = {}
    for m in re.finditer(
            r"INSERT INTO station \(.*?\)\s*VALUES \('(\w+)', '" + text + r"', (-?[\d.]+), (-?[\d.]+), (-?[\d.]+), "
            r"'([^']+)', (-?\d+), '(\w+)', '(\w+)', '(\w+)',\s*ARRAY\[([^\]]*)\]\) ON CONFLICT DO NOTHING;",
            sql, re.S):
        stations[m.group(1)] = dict(
            name=unquote(m.group(2)), lat=float(m.group(3)), lon=float(m.group(4)), elev=float(m.group(5)),
            tz=m.group(6), offset=int(m.group(7)), cli_pil=m.group(8), iem_id=m.group(9), network=m.group(10),
            rules_names=re.findall(r"'([^']*)'", m.group(11)))
    underlyings = {}
    for m in re.finditer(r"INSERT INTO underlying \(.*?\)\s*VALUES \('(\w+)', '" + text + r"', '(\w+)'\) ON CONFLICT DO NOTHING;",
                         sql, re.S):
        underlyings[m.group(1)] = dict(description=unquote(m.group(2)), station_id=m.group(3))
    series = {}
    for m in re.finditer(r"INSERT INTO series \(.*?\)\s*VALUES \('(\w+)', '(\w+)', '" + text + r"'\) ON CONFLICT DO NOTHING;",
                         sql, re.S):
        series[m.group(1)] = dict(underlying_id=m.group(2), title=unquote(m.group(3)))
    counts = {t: len(re.findall(r"^INSERT INTO " + t + r"\b", sql, re.M)) for t in ("station", "underlying", "series")}
    return stations, underlyings, series, counts


# ---------------------------------------------------------------- polite HTTP
last_call = {"iem": 0.0, "kalshi": 0.0}
requests_made = {"iem": 0, "kalshi": 0}


def get_json(kind, url, min_gap):
    for attempt in range(4):
        wait = min_gap - (time.monotonic() - last_call[kind])
        if wait > 0:
            time.sleep(wait)
        last_call[kind] = time.monotonic()
        requests_made[kind] += 1
        req = urllib.request.Request(url, headers={"User-Agent": "kalbt-probe"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.load(resp)
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 3:
                time.sleep(10)
                continue
            raise


def iem_network(network, cache={}):
    if network not in cache:
        data = get_json("iem", f"{IEM}?network={network}", 1.0)
        cache[network] = {f["id"]: f for f in data["features"]}
    return cache[network]


def event_day(market):
    """Event day from the event ticker (KXHIGHNY-26NOV22), None if it has no such day part."""
    parts = (market.get("event_ticker") or "").split("-", 1)
    if len(parts) < 2:
        return None
    try:
        return datetime.strptime(parts[1], "%y%b%d").date()
    except ValueError:
        return None


def window_markets(series_ticker):
    """All markets of the series whose event day is inside WINDOW (live and historical surfaces)."""
    found = {}
    for path in ("/markets", "/historical/markets"):
        cursor = ""
        while True:
            query = urllib.parse.urlencode({"series_ticker": series_ticker, "limit": 1000, "cursor": cursor})
            data = get_json("kalshi", f"{KALSHI}{path}?{query}", 0.5)
            for market in data.get("markets", []):
                day = event_day(market)
                if day is not None and WINDOW[0] <= day <= WINDOW[1]:
                    found[market["ticker"]] = market
            cursor = data.get("cursor") or ""
            if not cursor:
                break
    return found


# ---------------------------------------------------------------- docker helpers (as check_migrate.py)
def docker_sh(command, stdin=None):
    return subprocess.run(["docker", "exec", "-i", backup.CONTAINER, "sh", "-c", command],
                          stdin=stdin, capture_output=True, text=True)


def drop_db(name):
    docker_sh(f'dropdb --if-exists -U "$POSTGRES_USER" {name}')


def build_test_db():
    drop_db(TEST_DB)
    made = docker_sh(f'createdb -U "$POSTGRES_USER" {TEST_DB}')
    assert made.returncode == 0, made.stderr
    with open(SCHEMA, "rb") as f:
        loaded = docker_sh(f'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d {TEST_DB}', stdin=f)
    assert loaded.returncode == 0, loaded.stderr


def query(dbname, sql, params=None):
    with connect(dbname) as conn:
        return conn.execute(sql, params).fetchall()


def run_migrate_cli(dbname):
    return subprocess.run(["uv", "run", "python", "-m", "db.migrate", "--dbname", dbname],
                          cwd=ROOT, capture_output=True, text=True)


def table_counts(dbname):
    return {t: query(dbname, f"SELECT count(*) FROM {t}")[0][0] for t in ("station", "underlying", "series")}


def seed_rows(dbname):
    """The three rows that must never change, as plain tuples."""
    return (query(dbname, "SELECT * FROM station WHERE station_id = 'KNYC'"),
            query(dbname, "SELECT * FROM underlying WHERE underlying_id = 'NYC_HIGH'"),
            query(dbname, "SELECT * FROM series WHERE ticker = 'KXHIGHNY'"))


# ================================================================ run
stations, underlyings, series, file_counts = parse_migration(MIGRATION)
seeded = {sid: s for sid, s in stations.items() if sid != "KNYC"}
print(f"migration parsed: {file_counts}; seeded (without KNYC): {len(seeded)}")
report("0 parse sanity: stations, underlyings and series all counted, equal numbers",
       len(stations) == len(underlyings) == len(series) == file_counts["station"] == file_counts["underlying"]
       == file_counts["series"] > 0, (len(stations), len(underlyings), len(series), file_counts))

tmp = Path(tempfile.mkdtemp(prefix="kalbt_seed_qa_"))
try:
    # ---- 1. determinism: rerun the generator, compare byte for byte
    before_copy = tmp / "0002.before.sql"
    shutil.copy2(MIGRATION, before_copy)
    original_bytes = MIGRATION.read_bytes()
    try:
        run = subprocess.run(["uv", "run", "python", GENERATOR], cwd=ROOT, capture_output=True, text=True)
        same = run.returncode == 0 and MIGRATION.read_bytes() == original_bytes
        if not same:
            # keep the regenerated file for the report, then restore the applied one
            shutil.copy2(MIGRATION, tmp / "0002.regenerated.sql")
    finally:
        MIGRATION.write_bytes(original_bytes)
    detail = (run.returncode, run.stderr[-300:])
    if run.returncode == 0 and not same:
        diff = subprocess.run(["diff", str(before_copy), str(tmp / "0002.regenerated.sql")],
                              capture_output=True, text=True).stdout
        detail = "regenerated file differs:\n" + diff[:1500]
    report("1 rerunning the generator writes byte-identical SQL", same, detail)
    report("1b migration file restored to the original bytes", MIGRATION.read_bytes() == original_bytes)

    # ---- 2. station rows against IEM metadata
    bad = []
    for sid, s in stations.items():
        iem = s["iem_id"]
        if sid != "K" + iem:
            bad.append((sid, "station_id != K+iem_id"))
        if s["cli_pil"] != "CLI" + iem:
            bad.append((sid, "cli_pil", s["cli_pil"]))
        if sid != "KNYC" and s["rules_names"] != [f"(CLI{iem})"]:
            bad.append((sid, "rules_names", s["rules_names"]))
        if sid == "KNYC" and "(CLINYC)" not in s["rules_names"]:
            bad.append((sid, "KNYC rules_names lacks (CLINYC)"))
        feature = iem_network(s["network"]).get(iem)
        if feature is None:
            bad.append((sid, "no IEM feature in", s["network"]))
            continue
        lon, lat = feature["geometry"]["coordinates"]
        if abs(lat - s["lat"]) > 0.01 or abs(lon - s["lon"]) > 0.01:
            bad.append((sid, "coordinates", (s["lat"], s["lon"]), (lat, lon)))
        if feature["properties"]["tzname"] != s["tz"]:
            bad.append((sid, "tz", s["tz"], feature["properties"]["tzname"]))
        offset = int(ZoneInfo(s["tz"]).utcoffset(datetime(2026, 1, 15)).total_seconds() // 3600)
        if offset != s["offset"]:
            bad.append((sid, "std_utc_offset_h", s["offset"], offset))
    report(f"2 all {len(stations)} station rows match IEM metadata and the id conventions", not bad, bad)

    live_stations = {r[0]: r for r in query(
        "kalbt", "SELECT station_id, latitude, longitude, tz_name, std_utc_offset_h, cli_pil, iem_id, rules_names "
                 "FROM station")}
    bad = []
    for sid, s in seeded.items():
        row = live_stations.get(sid)
        if row is None:
            bad.append((sid, "missing live"))
        elif (float(row[1]), float(row[2]), row[3], row[4], row[5], row[6], row[7]) != (
                s["lat"], s["lon"], s["tz"], s["offset"], s["cli_pil"], s["iem_id"], s["rules_names"]):
            bad.append((sid, "live row differs from file", row))
    report("2b live station rows equal the migration file rows", not bad, bad)

    # ---- 3. rules text of the window's markets names the right code
    bad = []
    market_counts = {}
    for ticker, s in series.items():
        iem = stations[underlyings[s["underlying_id"]]["station_id"]]["iem_id"]
        markets = window_markets(ticker)
        market_counts[ticker] = len(markets)
        codes = set()
        for market in markets.values():
            codes.update(CODE.findall(market.get("rules_primary") or ""))
        if not markets:
            bad.append((ticker, "no markets in window"))
        elif iem not in codes:
            bad.append((ticker, "no market names", f"(CLI{iem})", sorted(codes)))
        elif codes != {iem}:
            bad.append((ticker, "other codes in window", sorted(codes)))
    print(f"markets in window per series: {market_counts}; requests: {requests_made}")
    report(f"3 all {len(series)} series: window markets name exactly their own CLI code", not bad, bad)

    # ---- 4. no shared underlying; KNYC rows unchanged
    underlying_of = [s["underlying_id"] for s in series.values()]
    report("4a no two seeded series share an underlying", len(underlying_of) == len(set(underlying_of)),
           sorted(u for u in set(underlying_of) if underlying_of.count(u) > 1))
    shared = query("kalbt", "SELECT underlying_id, count(*) FROM series GROUP BY 1 HAVING count(*) > 1")
    report("4b live: no underlying has two series", not shared, shared)
    live_knyc = query("kalbt", "SELECT name, latitude, longitude, elevation_m, tz_name, std_utc_offset_h, cli_pil, "
                               "iem_id, iem_network, rules_names FROM station WHERE station_id = 'KNYC'")
    expected_knyc = ("NEW YORK CITY", 40.779, -73.9692, 27.0, "America/New_York", -5, "CLINYC", "NYC", "NY_ASOS",
                     NYC_RULES_NAMES)
    report("4c live: KNYC keeps its values and its three rules_names",
           len(live_knyc) == 1 and (live_knyc[0][0], float(live_knyc[0][1]), float(live_knyc[0][2]),
                                    float(live_knyc[0][3])) == expected_knyc[:4] and tuple(live_knyc[0][4:]) == expected_knyc[4:],
           live_knyc)
    report("4d live: NYC_HIGH -> KNYC and KXHIGHNY -> NYC_HIGH",
           query("kalbt", "SELECT station_id FROM underlying WHERE underlying_id = 'NYC_HIGH'") == [("KNYC",)]
           and query("kalbt", "SELECT underlying_id FROM series WHERE ticker = 'KXHIGHNY'") == [("NYC_HIGH",)])

    # ---- 5. throwaway database
    build_test_db()
    fresh_counts = table_counts(TEST_DB)
    report("5a fresh build from db/schema.sql has no seed rows", set(fresh_counts.values()) == {0}, fresh_counts)
    with open(SCHEMA_TESTS, "rb") as f:
        schema_run = docker_sh(f'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d {TEST_DB}', stdin=f)
    report("5b tests/db/test_schema.sql passes on the fresh build", schema_run.returncode == 0
           and "FAIL" not in schema_run.stdout + schema_run.stderr,
           (schema_run.returncode, (schema_run.stdout + schema_run.stderr)[-500:]))
    first = run_migrate_cli(TEST_DB)
    after_first = table_counts(TEST_DB)
    report("5c first migrate applies 0002", first.returncode == 0 and "applied 0002_us_daily_high_stations" in first.stdout,
           (first.returncode, first.stdout, first.stderr[-300:]))
    report("5d row counts equal the file's INSERT counts",
           after_first == {t: fresh_counts[t] + file_counts[t] for t in fresh_counts}, (after_first, file_counts))
    report("5e schema_migration records 0002 exactly once",
           query(TEST_DB, "SELECT count(*) FROM schema_migration WHERE id = '0002_us_daily_high_stations'") == [(1,)])
    second = run_migrate_cli(TEST_DB)
    report("5f second migrate applies nothing, counts unchanged",
           second.returncode == 0 and "applied" not in second.stdout and "nothing to apply" in second.stdout
           and table_counts(TEST_DB) == after_first, (second.returncode, second.stdout, second.stderr[-300:]))
    # extra: re-running the file by hand is idempotent (ON CONFLICT DO NOTHING)
    with connect(TEST_DB) as conn:
        conn.execute(MIGRATION.read_text())
    report("5g extra: executing the file again changes no row counts", table_counts(TEST_DB) == after_first)
    # extra: the throwaway rows equal the live rows for every seeded station
    fresh_rows = query(TEST_DB, "SELECT station_id, latitude, longitude, tz_name, std_utc_offset_h, cli_pil, iem_id "
                                "FROM station WHERE station_id <> 'KNYC' ORDER BY 1")
    live_rows = query("kalbt", "SELECT station_id, latitude, longitude, tz_name, std_utc_offset_h, cli_pil, iem_id "
                               "FROM station WHERE station_id <> 'KNYC' ORDER BY 1")
    report("5h extra: throwaway seeded stations equal the live ones", fresh_rows == live_rows)

    # ---- 6. live database
    live_migrations = [r[0] for r in query("kalbt", "SELECT id FROM schema_migration")]
    report("6a live schema_migration holds 0002_us_daily_high_stations",
           "0002_us_daily_high_stations" in live_migrations, live_migrations)
    live_station_count = query("kalbt", "SELECT count(*) FROM station")[0][0]
    report("6b live station count = 1 + seeded count (KNYC once)", live_station_count == 1 + len(seeded),
           (live_station_count, len(seeded)))
    live_series = {r[0] for r in query("kalbt", "SELECT ticker FROM series")}
    report("6c extra: every seeded series and underlying is live",
           set(series) <= live_series and set(underlyings) <= {r[0] for r in query("kalbt", "SELECT underlying_id FROM underlying")})
finally:
    drop_db(TEST_DB)
    shutil.rmtree(tmp, ignore_errors=True)

print(f"requests: {requests_made}")
if failures:
    print(f"\n{len(failures)} check(s) FAILED: {failures}")
    raise SystemExit(1)
print("\nall US seed checks passed")
