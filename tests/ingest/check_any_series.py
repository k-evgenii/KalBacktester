"""Checks market dump, gate and loaders for any seeded series, proved on Chicago (spec A020, tests 1 to 7).

Tampered copies live in temporary folders. A loader is only run on a copy that is shown first to
be blocked (test 7) or to name an unseeded series (test 6), so nothing tampered can reach the
append-only live database. Everything else is read-only.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from datetime import date
from pathlib import Path

from db.connect import connect
from ingest.load_kxhighny_dump import series_of
from ingest.qa_kxhighny_dump import run_checks

ROOT = Path(__file__).resolve().parent.parent.parent
CHI_DUMP = ROOT / "api_probe" / "kalshi" / "KXHIGHCHI_2026-11-16_2026-11-22"
NY_DUMP = ROOT / "api_probe" / "kalshi" / "KXHIGHNY"
KNYC_WEATHER = ROOT / "api_probe" / "weather" / "dump" / "KNYC"
TABLES = ["underlying", "series", "event", "market", "candle", "official_daily",
          "observation_hourly", "forecast_hourly"]

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def row_counts():
    conn = connect()
    cur = conn.cursor()
    counts = {}
    for table in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    conn.close()
    return counts


def checks_by_id(rep):
    return {c["id"]: c for c in rep["checks"]}


def run_loader(args):
    return subprocess.run([sys.executable, "-m", *args], cwd=ROOT, capture_output=True, text=True)


conn = connect()
cur = conn.cursor()

# Test 1
one = [{"event_ticker": "KXHIGHCHI-26NOV16"}, {"event_ticker": "KXHIGHCHI-26NOV17"}]
report("1a one series -> that series", series_of(one) == "KXHIGHCHI")
try:
    series_of(one + [{"event_ticker": "KXHIGHNY-26NOV16"}])
    report("1b two series -> ValueError", False, "no error")
except ValueError:
    report("1b two series -> ValueError", True)

# Test 2
rep = run_checks(CHI_DUMP, conn)
c = checks_by_id(rep)
report("2a Chicago dump: verdict PASS", rep["verdict"] == "PASS", str(rep["verdict"]))
report("2b checks 22 and 23 pass", c[22]["status"] == "pass" and c[23]["status"] == "pass", f"{c[22]} {c[23]}")
raw = (CHI_DUMP / "markets.json").read_text()
report("2c the Chicago rules text names (CLIMDW)", "(CLIMDW)" in raw)
with tempfile.TemporaryDirectory() as tmp:
    copy = Path(tmp) / "KXHIGHCHI"
    shutil.copytree(CHI_DUMP, copy, ignore=shutil.ignore_patterns("_qa_report.json"))  # keep _report.json: "trades": false
    (copy / "markets.json").write_text(raw.replace("(CLIMDW)", "(CLINYC)"))
    c22 = checks_by_id(run_checks(copy, conn))[22]
    report("2d check 22 blocks when the rules name (CLINYC) instead of KMDW's (CLIMDW)",
           c22["status"] == "fail" and c22["severity"] == "BLOCK", str(c22))
report("2e check 14 passes or warns only", c[14]["status"] == "pass" or c[14]["severity"] == "WARN", str(c[14]))
report("2f check 14 passes on Chicago", c[14]["status"] == "pass", str(c[14]))

# Test 3
with tempfile.TemporaryDirectory() as tmp:
    copy = Path(tmp) / "KXHIGHNY"
    shutil.copytree(NY_DUMP, copy, ignore=shutil.ignore_patterns("_report.json", "_qa_report.json"))
    data = json.loads((copy / "markets.json").read_text())
    data["markets"][0]["event_ticker"] = "KXHIGHCHI-26NOV16"
    (copy / "markets.json").write_text(json.dumps(data))
    rep = run_checks(copy, conn)
    c1 = checks_by_id(rep)[1]
    report("3 two series: check 1 BLOCK, verdict BLOCK",
           c1["status"] == "fail" and c1["severity"] == "BLOCK" and rep["verdict"] == "BLOCK",
           f"{c1} {rep['verdict']}")

# Test 4
chi_markets = json.loads((CHI_DUMP / "markets.json").read_text())["markets"]
dump_count = {}
for m in chi_markets:
    dump_count[m["event_ticker"]] = dump_count.get(m["event_ticker"], 0) + 1
cur.execute("SELECT ticker, event_date, underlying_id FROM event WHERE series_ticker = 'KXHIGHCHI' "
            "AND event_date BETWEEN '2026-11-16' AND '2026-11-22' ORDER BY event_date")
events = cur.fetchall()
report("4a 7 KXHIGHCHI events for 2026-11-16..22",
       [e[1] for e in events] == [date(2026, 11, d) for d in range(16, 23)], str(events))
report("4b all with underlying MDW_HIGH", all(e[2] == "MDW_HIGH" for e in events) and len(events) == 7, str(events))
cur.execute("SELECT m.event_ticker, count(*), count(*) FILTER (WHERE m.result = 'yes') FROM market m "
            "JOIN event e ON e.ticker = m.event_ticker "
            "WHERE e.series_ticker = 'KXHIGHCHI' AND e.event_date BETWEEN '2026-11-16' AND '2026-11-22' "
            "GROUP BY m.event_ticker")
db_markets = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
bad = {e: v for e, v in db_markets.items() if v != (dump_count.get(e), 1)}
report("4c every event has the dump's market count and exactly one yes",
       not bad and set(db_markets) == set(dump_count) and len(dump_count) == 7, f"{bad} {db_markets}")
cur.execute("SELECT count(*) FROM candle c JOIN market m ON m.ticker = c.market_ticker "
            "WHERE m.event_ticker LIKE 'KXHIGHCHI-%'")
n_candles = cur.fetchone()[0]
cur.execute("SELECT count(*) FROM candle c JOIN market m ON m.ticker = c.market_ticker "
            "WHERE m.event_ticker LIKE 'KXHIGHCHI-%' AND (c.ts < m.open_time OR c.ts > m.close_time)")
outside = cur.fetchone()[0]
report("4d candles exist and every ts is within open..close", n_candles > 0 and outside == 0,
       f"{n_candles} candles, {outside} outside")

# Test 5
cur.execute("SELECT climate_date, source, high_f FROM official_daily WHERE station_id = 'KMDW' "
            "AND climate_date BETWEEN '2026-11-16' AND '2026-11-22'")
by_source = {"nws_cli": {}, "kalshi": {}}
for day, source, high in cur.fetchall():
    by_source[source][day] = high
report("5a KMDW has 7 nws_cli and 7 kalshi rows", len(by_source["nws_cli"]) == 7 and len(by_source["kalshi"]) == 7,
       f"{len(by_source['nws_cli'])} / {len(by_source['kalshi'])}")
report("5b nws_cli equals kalshi on every day", by_source["nws_cli"] == by_source["kalshi"],
       f"{by_source['nws_cli']} vs {by_source['kalshi']}")
cur.execute("SELECT e.event_date, m.strike_type, m.floor_strike, m.cap_strike FROM event e "
            "JOIN market m ON m.event_ticker = e.ticker "
            "WHERE e.series_ticker = 'KXHIGHCHI' AND m.result = 'yes' "
            "AND e.event_date BETWEEN '2026-11-16' AND '2026-11-22'")
bad = []
wins = cur.fetchall()
for day, strike_type, floor, cap in wins:
    high = by_source["kalshi"].get(day)
    if high is None:
        bad.append((day, "no kalshi value"))
    elif strike_type == "less":
        ok = high < cap
    elif strike_type == "greater":
        ok = high > floor
    else:
        ok = floor <= high <= cap
    if high is not None and not ok:
        bad.append((day, strike_type, floor, cap, high))
report("5c kalshi high falls in the winning market's strike range (7 events)", not bad and len(wins) == 7,
       f"{bad} ({len(wins)} winners)")
conn.close()

# Test 6
conn = connect()
cur = conn.cursor()
cur.execute("SELECT count(*) FROM series WHERE ticker = 'KXHIGHZZZ'")
assert cur.fetchone()[0] == 0, "KXHIGHZZZ must not be seeded for this test"
conn.close()
before = row_counts()
with tempfile.TemporaryDirectory() as tmp:
    copy = Path(tmp) / "KXHIGHZZZ"
    shutil.copytree(NY_DUMP, copy, ignore=shutil.ignore_patterns("_report.json", "_qa_report.json"))
    for path in sorted(copy.rglob("*"), reverse=True):
        if path.is_file() and path.suffix == ".json":
            path.write_text(path.read_text().replace("KXHIGHNY", "KXHIGHZZZ"))
        if "KXHIGHNY" in path.name:
            path.rename(path.with_name(path.name.replace("KXHIGHNY", "KXHIGHZZZ")))
    proc = run_loader(["ingest.load_kxhighny_dump", str(copy)])
    out = proc.stdout + proc.stderr
    report("6a unseeded series: loader exits 1", proc.returncode == 1, f"exit {proc.returncode}: {out[-300:]}")
    report("6b message says not seeded", "not seeded" in out and "KXHIGHZZZ" in out, out[-300:])
report("6c row counts unchanged", row_counts() == before)

# Test 7
before = row_counts()
with tempfile.TemporaryDirectory() as tmp:
    copy = Path(tmp) / "KNYC"
    shutil.copytree(KNYC_WEATHER, copy)
    cli_path = copy / "cli_2026.json"
    cli = json.loads(cli_path.read_text())
    day = next(r for r in cli if r["valid"] == "2026-11-17")
    day["high"] = day["high"] + 1
    cli_path.write_text(json.dumps(cli))
    args = ["--station", "KNYC", "--dump-dir", str(copy), "--markets", str(NY_DUMP / "markets.json")]

    # prove the copy is blocked before any loader touches it
    from ingest.load_weather_dump import cli_rows, kalshi_values, numeric_verdict
    kalshi = kalshi_values(NY_DUMP / "markets.json")
    nws = {r[1]: r[3] for r in cli_rows("KNYC", copy)}
    days = [d for d in kalshi if d in nws]
    mismatches = [d for d in days if kalshi[d] != nws[d]]
    verdict = numeric_verdict(len(days), len(mismatches))
    report("7a precondition: numeric check only warns", len(mismatches) == 1 and verdict == "warn",
           f"{mismatches} {verdict}")
    conn = connect()
    from ingest.qa_weather_dump import run_checks as weather_gate
    gate = weather_gate(conn, "KNYC", copy, NY_DUMP / "markets.json")
    conn.close()
    report("7b precondition: weather QA gate blocks the copy", gate["verdict"] != "PASS", str(gate["verdict"]))
    if verdict == "warn" and gate["verdict"] != "PASS":
        proc = run_loader(["ingest.load_weather_dump", *args])
        out = proc.stdout + proc.stderr
        report("7c loader exits 1", proc.returncode == 1, f"exit {proc.returncode}: {out[-300:]}")
        report("7d prints 'weather QA gate: BLOCK, nothing inserted'",
               "weather QA gate: BLOCK, nothing inserted" in out, out[-300:])
        report("7e numeric check only warned", "-> warn" in out, out[-300:])
        report("7f no 'rows inserted' line", "rows inserted" not in out, out[-300:])
    else:
        report("7c-f loader not run: copy not proven blocked", False)
report("7g row counts unchanged", row_counts() == before)

print(f"{len(failures)} failed")
sys.exit(1 if failures else 0)
