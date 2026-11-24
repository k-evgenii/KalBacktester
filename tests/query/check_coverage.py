"""Checks for spec A023 (query/coverage.py), agreed tests 1 to 7.

Run from the repo root: uv run python -m tests.query.check_coverage
Read-only on live kalbt. Only fixed facts are asserted (a backfill may still be loading other weeks).
"""
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from db.connect import connect
from query.coverage import coverage, status

ROOT = Path(__file__).resolve().parent.parent.parent
failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + str(detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def pick(rows, city, week):
    found = [r for r in rows if r["underlying_id"] == city and r["week_start"] == week]
    return found[0] if len(found) == 1 else None


def check_fields(name, row, expected):
    if row is None:
        report(name, False, "row missing or duplicated")
        return
    got = {k: row[k] for k in expected}
    report(name, got == expected, f"got {got}, expected {expected}")


# Test 1: status table
cases = [((7, 7, 7, 0), "OK"), ((6, 6, 6, 0), "RED"), ((7, 7, 7, 2), "RED"), ((7, 7, 7, 1), "Check"),
         ((7, 6, 7, 0), "Check"), ((7, 7, 6, 0), "Check"), ((0, 0, 0, 0), "RED")]
for args, want in cases:
    report(f"1 status{args} == {want}", status(*args) == want, status(*args))

conn = connect()
conn.read_only = True
rows = coverage(conn)

# Test 2: NYC_HIGH November week
check_fields("2 NYC_HIGH 2026-11-16", pick(rows, "NYC_HIGH", date(2026, 11, 16)),
             {"events": 7, "markets": 42, "bars": 41158, "nws_highs": 7, "kalshi_highs": 7,
              "numeric_mismatches": 0, "status": "OK", "gate_warnings": None})

# Test 3: NYC_HIGH June week, gate_warnings read from the QA report file by the test itself
june = pick(rows, "NYC_HIGH", date(2026, 6, 1))
report_file = ROOT / "api_probe" / "kalshi" / "KXHIGHNY_2026-06-01_2026-06-07" / "_qa_report.json"
try:
    checks = json.loads(report_file.read_text())["checks"]
    expected_warn = sum(1 for c in checks if c.get("severity") == "WARN" and c.get("status") == "fail")
except (OSError, ValueError, KeyError):
    expected_warn = None
check_fields("3 NYC_HIGH 2026-06-01 events/status/gate_warnings", june,
             {"events": 7, "status": "OK", "gate_warnings": expected_warn})

# Test 4: MDW_HIGH November week
check_fields("4 MDW_HIGH 2026-11-16", pick(rows, "MDW_HIGH", date(2026, 11, 16)),
             {"events": 7, "kalshi_highs": 7, "nws_highs": 7, "numeric_mismatches": 0, "status": "OK"})

# Test 5: all window Mondays for every seeded city, Mondays only, ordered
mondays = [date(2026, 8, 24) + timedelta(days=7 * i) for i in range(13)]
with conn.cursor() as cur:
    cur.execute("SELECT u.underlying_id FROM underlying u JOIN series s USING (underlying_id) "
                "WHERE u.station_id IS NOT NULL")
    cities = sorted({r[0] for r in cur.fetchall()})
by_city = {}
for r in rows:
    by_city.setdefault(r["underlying_id"], set()).add(r["week_start"])
missing = {c: [m for m in mondays if m not in by_city.get(c, set())] for c in cities}
missing = {c: v for c, v in missing.items() if v}
report("5a every seeded city has all 13 window Mondays", len(cities) > 0 and not missing, missing)
report("5b every week_start is a Monday", all(r["week_start"].weekday() == 0 for r in rows))
keys = [(r["underlying_id"], r["week_start"]) for r in rows]
report("5c rows ordered by underlying_id then week_start, no duplicates",
       keys == sorted(keys) and len(keys) == len(set(keys)))

# Test 6: EWR_HIGH 2026-08-24 has fewer than 7 events -> RED
ewr = pick(rows, "EWR_HIGH", date(2026, 8, 24))
report("6 EWR_HIGH 2026-08-24 events < 7 and RED", ewr is not None and ewr["events"] < 7 and ewr["status"] == "RED", ewr)

# Test 7: nothing written (read-only transaction accepted the call; a write attempt is refused)
conn.rollback()
conn.read_only = True
refused = False
try:
    coverage(conn)
    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _a023_probe (x int)")
except Exception as exc:
    refused = "read-only" in str(exc).lower()
conn.rollback()
conn.close()
report("7 coverage runs in a read-only transaction; writes are refused there", refused)

print()
print("FAILED: " + ", ".join(failures) if failures else "all checks passed")
sys.exit(1 if failures else 0)
