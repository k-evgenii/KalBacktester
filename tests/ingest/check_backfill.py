"""Checks the backfill runner (spec A021, tests 1 to 5).

Run: uv run python -m tests.ingest.check_backfill

Read-only against the live database. The only live runs are the ones the runner already made:
KXHIGHMIA 2026-11-16..22 (loaded, test 3) is verified from the database, the log and the backup file,
and rerun (test 4) where it must skip and touch nothing. Test 5 cannot find a real week that
blocks (see its comment), so it checks the real error week from the log and then the blocked
path with fake step commands, a temporary log and no backup.
"""
import contextlib
import io
import json
import subprocess
import sys
from datetime import date
from pathlib import Path
from unittest import mock

from db.connect import connect
from ingest import backfill

ROOT = Path(__file__).resolve().parent.parent.parent
LOG = ROOT / "ingest" / "backfill_log.jsonl"
BACKUPS = ROOT / "backups" / "db"
TABLES = ["underlying", "series", "event", "market", "candle", "official_daily",
          "observation_hourly", "forecast_hourly"]

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def row_counts():
    conn = connect()
    counts = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    conn.close()
    return counts


def log_lines():
    return LOG.read_text().splitlines() if LOG.exists() else []


def backup_files():
    return sorted(p.name for p in BACKUPS.glob("*.dump"))


def run_backfill(*args):
    return subprocess.run([sys.executable, "-m", "ingest.backfill", *args], cwd=ROOT,
                          capture_output=True, text=True)


# Test 1: dry run
counts_before, log_before, backups_before = row_counts(), log_lines(), backup_files()
dry = run_backfill("--dry")
lines = dry.stdout.splitlines()
plan = [ln.split() for ln in lines if ln.endswith(" skip") or ln.endswith(" todo")]
cities = {p[0] for p in plan}
skips = sorted((p[0], p[1]) for p in plan if p[2] == "skip")
week = "2026-11-16..2026-11-22"
report("1a dry exits 0", dry.returncode == 0, dry.stderr[-300:])
report("1b 24 cities x 13 weeks", len(cities) == 24 and len(plan) == 24 * 13, f"{len(cities)} cities {len(plan)} lines")
# The backfill keeps loading weeks, so assert fixed facts only: the three originally loaded weeks skip,
# and the totals line equals the counted skip and todo lines.
skip_set = set(skips)
report("1c NY, CHI, MIA 16-22 Nov are among the skips",
       {("KXHIGHNY", week), ("KXHIGHCHI", week), ("KXHIGHMIA", week)} <= skip_set, str(skips))
n_skip = len(skips)
n_todo = len(plan) - n_skip
report("1d totals line equals counted skip and todo lines (312 in total)",
       lines[-1] == f"totals: skip {n_skip}, todo {n_todo}" and n_skip + n_todo == 312,
       f"{lines[-1]} counted {n_skip} {n_todo}")
report("1e dry writes nothing (rows, log, backups)",
       row_counts() == counts_before and log_lines() == log_before and backup_files() == backups_before)

# Test 2: week arithmetic
bad_start = run_backfill("--dry", "--start", "2026-08-25")
bad_end = run_backfill("--dry", "--end", "2026-11-21")
report("2a start not Monday -> exit 1 with message",
       bad_start.returncode == 1 and "Monday" in bad_start.stderr, bad_start.stderr[-200:])
report("2b end not Sunday -> exit 1 with message",
       bad_end.returncode == 1 and "Sunday" in bad_end.stderr, bad_end.stderr[-200:])
weeks = backfill.list_weeks(date(2026, 8, 24), date(2026, 11, 22))
report("2c 13 weeks, Monday..Sunday, first and last right",
       len(weeks) == 13 and weeks[0] == (date(2026, 8, 24), date(2026, 8, 30))
       and weeks[-1] == (date(2026, 11, 16), date(2026, 11, 22))
       and all(m.weekday() == 0 and s.weekday() == 6 for m, s in weeks))

# Test 3: Miami loaded (verified from the database, the log and the backup file)
conn = connect()
rows = conn.execute(
    "SELECT climate_date, source, high_f FROM official_daily WHERE station_id = 'KMIA' "
    "AND climate_date BETWEEN '2026-11-16' AND '2026-11-22'").fetchall()
kalshi = {r[0]: r[2] for r in rows if r[1] == "kalshi"}
nws = {r[0]: r[2] for r in rows if r[1] == "nws_cli"}
events = conn.execute("SELECT count(*) FROM event WHERE series_ticker = 'KXHIGHMIA' "
                      "AND event_date BETWEEN '2026-11-16' AND '2026-11-22'").fetchone()[0]
conn.close()
report("3a KMIA 7 kalshi and 7 nws_cli rows", len(kalshi) == 7 and len(nws) == 7, f"{len(kalshi)} {len(nws)}")
report("3b kalshi == nws_cli on every day", kalshi == nws, f"{kalshi} {nws}")
report("3c 7 KXHIGHMIA events", events == 7, str(events))
mia_log = [json.loads(ln) for ln in log_lines()
           if json.loads(ln)["series"] == "KXHIGHMIA" and json.loads(ln)["monday"] == "2026-11-16"]
report("3d exactly one Miami log line, status loaded, failed_step null",
       len(mia_log) == 1 and mia_log[0]["status"] == "loaded" and mia_log[0]["failed_step"] is None, str(mia_log)[:300])
report("3e one backup *_before-backfill-KXHIGHMIA.dump",
       len([b for b in backup_files() if b.endswith("_before-backfill-KXHIGHMIA.dump")]) == 1)

# Test 4: rerun of the Miami command skips and changes nothing
counts_before, log_before, backups_before = row_counts(), log_lines(), backup_files()
dumps_before = sorted(p.name for p in (ROOT / "api_probe" / "kalshi").iterdir())
rerun = run_backfill("--series", "KXHIGHMIA", "--start", "2026-11-16", "--end", "2026-11-22")
report("4a rerun exits 0, prints totals with skip 1 only",
       rerun.returncode == 0 and rerun.stdout.strip() == "totals: skip 1", rerun.stdout + rerun.stderr[-200:])
report("4b no backup taken", backup_files() == backups_before)
report("4c no log line written", log_lines() == log_before)
report("4d no rows changed", row_counts() == counts_before)
report("4e nothing fetched (no new dump folder)",
       sorted(p.name for p in (ROOT / "api_probe" / "kalshi").iterdir()) == dumps_before)

# Test 5: a blocked week is logged, not fatal.
# No real week blocks. TEWR and TSDF have no market on 24 and 25 Aug; every other week of theirs is
# complete (live and historical lists checked). That week fails earlier, at the dump (step 0,
# "no markets found for event"), so it is logged as `error`, not `blocked`. The runner was run
# once on it (exit 0); its log line is checked here, then the blocked path is run with fake steps.
tewr = [json.loads(ln) for ln in log_lines()
        if json.loads(ln)["series"] == "KXHIGHTEWR" and json.loads(ln)["monday"] == "2026-08-24"]
report("5a real TEWR 24-30 Aug week logged (error at step 0, no markets)",
       len(tewr) >= 1 and tewr[-1]["status"] == "error" and tewr[-1]["failed_step"] == 0
       and "no markets found" in tewr[-1]["tail"], str(tewr)[:300])
conn = connect()
tewr_rows = conn.execute("SELECT count(*) FROM event WHERE series_ticker = 'KXHIGHTEWR' "
                         "AND event_date BETWEEN '2026-08-24' AND '2026-08-30'").fetchone()[0]
conn.close()
report("5b nothing inserted for the TEWR week", tewr_rows == 0, str(tewr_rows))

# Simulated: week 1 blocks at load step 1 (output has BLOCK), week 2 loads. Fake steps, temp log, fake backup.
fake_log = ROOT / "tests" / "ingest" / "_fake_backfill_log.jsonl"
fake_log.unlink(missing_ok=True)
py = sys.executable


def fake_steps(series, station, network, monday, sunday):
    if str(monday) == "2026-08-24":
        return [[py, "-c", "pass"], [py, "-c", "print('QA gate: BLOCK'); raise SystemExit(1)"]]
    return [[py, "-c", "print('ok')"], [py, "-c", "print('fine')"]]


counts_before, backups_before, log_before = row_counts(), backup_files(), log_lines()
out = io.StringIO()
with mock.patch.object(backfill, "LOG_FILE", fake_log), \
        mock.patch.object(backfill, "build_steps", fake_steps), \
        mock.patch.object(backfill, "dump", lambda reason: Path("fake.dump")), \
        mock.patch.object(sys, "argv", ["backfill", "--series", "KXHIGHTEWR",
                                        "--start", "2026-08-24", "--end", "2026-09-06"]), \
        contextlib.redirect_stdout(out):
    exit_code = None
    try:
        backfill.main()
    except SystemExit as e:
        exit_code = e.code
entries = [json.loads(ln) for ln in fake_log.read_text().splitlines()] if fake_log.exists() else []
fake_log.unlink(missing_ok=True)
report("5c simulated blocked week: exit 0, run continues to the next week",
       exit_code is None and len(entries) == 2, f"{exit_code} {len(entries)}")
report("5d week 1 blocked at step 1, week 2 loaded",
       [e["status"] for e in entries] == ["blocked", "loaded"] and entries[0]["failed_step"] == 1
       and entries[1]["failed_step"] is None, str(entries)[:300])
report("5e blocked tail holds the BLOCK line", entries and "BLOCK" in entries[0]["tail"])
report("5f totals line counts both", "totals: blocked 1, loaded 1" in out.getvalue(), out.getvalue())
report("5g simulation touched no live rows, backups or real log",
       row_counts() == counts_before and backup_files() == backups_before and log_lines() == log_before)

# Classification rules from the spec corrections
with mock.patch.object(backfill, "build_steps", lambda *a: [[py, "-c", "print('BLOCK'); raise SystemExit(1)"]]):
    status, step, _ = backfill.run_week("S", "K", "N", date(2026, 8, 24), date(2026, 8, 30))
report("5h BLOCK text on a non-load step (index 0) is error", (status, step) == ("error", 0), f"{status} {step}")
with mock.patch.object(backfill, "build_steps", lambda *a: [[py, "-c", "pass"], [py, "-c", "raise SystemExit(1)"]]):
    status, step, _ = backfill.run_week("S", "K", "N", date(2026, 8, 24), date(2026, 8, 30))
report("5i load step failing without BLOCK is error", (status, step) == ("error", 1), f"{status} {step}")


def timeout(*a, **k):
    raise subprocess.TimeoutExpired("x", 1)


with mock.patch.object(backfill, "build_steps", lambda *a: [[py, "-c", "pass"]]), \
        mock.patch.object(backfill.subprocess, "run", timeout):
    status, step, tail = backfill.run_week("S", "K", "N", date(2026, 8, 24), date(2026, 8, 30))
report("5j timeout is error", (status, step) == ("error", 0) and "timeout" in tail, f"{status} {step} {tail}")

print()
print("FAILED: " + ", ".join(failures) if failures else "all checks passed")
sys.exit(1 if failures else 0)
