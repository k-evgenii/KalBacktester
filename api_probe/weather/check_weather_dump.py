"""Checks the KNYC weather dump against the agreed tests of spec 006 (tests 1-6).

Run from the repo root: uv run python api_probe/weather/check_weather_dump.py
Reads only the raw dump files, never _report.json.
"""
import csv
import json
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

DUMP = Path("api_probe/weather/dump/KNYC")
MARKETS = Path("api_probe/kalshi/KXHIGHNY/markets.json")
START = date(2026, 11, 16)
END = date(2026, 11, 22)
STD_OFFSET = timedelta(hours=-5)  # America/New_York standard time

LEADS = {
    "ecmwf_ifs025": 7, "gfs_seamless": 7, "gem_seamless": 7, "jma_seamless": 7,
    "ncep_nbm_conus": 7, "icon_seamless": 6, "ukmo_seamless": 6,
    "meteofrance_seamless": 3, "gfs_hrrr": 1, "ecmwf_aifs025": None,
}
VARIABLES = ["temperature_2m", "dew_point_2m", "cloud_cover", "wind_speed_10m",
             "precipitation", "shortwave_radiation"]

results = []


def record(name, ok, reason):
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}: {reason}")


def days():
    d = START
    while d <= END:
        yield d
        d += timedelta(days=1)


def window(day):
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - STD_OFFSET
    return start, start + timedelta(hours=24)


def load_cli():
    rows = json.loads((DUMP / "cli_2026.json").read_text())
    return [r for r in rows]


def load_asos():
    """Return list of (utc datetime, tmpf float or None) per row."""
    out = []
    with open(DUMP / "asos.csv", newline="") as f:
        for row in csv.DictReader(f):
            t = datetime.strptime(row["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            raw = row["tmpf"].strip()
            out.append((t, raw))
    return out


def tmpf_value(raw):
    """float, or None for M; raises ValueError for anything else."""
    if raw == "M":
        return None
    return float(raw)


def test1_station():
    name = "1 station.json is KNYC"
    f = json.loads((DUMP / "station.json").read_text())
    lon, lat = f["geometry"]["coordinates"][:2]
    problems = []
    if f.get("id") != "NYC":
        problems.append(f"id={f.get('id')}")
    if f["properties"].get("tzname") != "America/New_York":
        problems.append(f"tzname={f['properties'].get('tzname')}")
    if abs(lat - 40.779) > 0.01 or abs(lon - (-73.969)) > 0.01:
        problems.append(f"lat/lon={lat},{lon}")
    record(name, not problems, "; ".join(problems) or f"id NYC, America/New_York, lat/lon {lat},{lon}")


def test2_cli(cli):
    name = "2 CLI rows final, one per day"
    problems = []
    for d in days():
        rows = [r for r in cli if r.get("valid") == d.isoformat()]
        if len(rows) != 1:
            problems.append(f"{d}: {len(rows)} rows")
            continue
        r = rows[0]
        if not isinstance(r.get("high"), int) or isinstance(r.get("high"), bool):
            problems.append(f"{d}: high={r.get('high')!r} not int")
        if not r.get("high_time"):
            problems.append(f"{d}: no high_time")
        product = r.get("product")
        if not product:
            problems.append(f"{d}: no product")
            continue
        issued = datetime.strptime(product[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
        end = window(d)[1]
        if issued < end:
            problems.append(f"{d}: product {product} issued {issued:%Y-%m-%d %H:%M}Z before window end {end:%Y-%m-%d %H:%M}Z (preliminary)")
    record(name, not problems, "; ".join(problems) or "7 days, int high, high_time, product issued after window end")


def test3_asos(asos):
    name = "3 ASOS hourly coverage and tmpf format"
    problems = []
    for d in days():
        start, end = window(d)
        hours = {t.replace(minute=0, second=0, microsecond=0) for t, _ in asos if start <= t < end}
        missing = [(start + timedelta(hours=h)) for h in range(24) if (start + timedelta(hours=h)) not in hours]
        if missing:
            problems.append(f"{d}: missing UTC hours {[m.strftime('%d %H') for m in missing]}")
    bad = []
    for t, raw in asos:
        try:
            tmpf_value(raw)
        except ValueError:
            bad.append((t.isoformat(), raw))
    if bad:
        problems.append(f"non-numeric tmpf: {bad[:5]}")
    record(name, not problems, "; ".join(problems) or f"{len(asos)} rows, 24 hours covered every day, tmpf numeric or M")


def test4_cli_vs_asos(cli, asos):
    name = "4 CLI high within 2F of ASOS max"
    problems = []
    notes = []
    for d in days():
        start, end = window(d)
        vals = [tmpf_value(raw) for t, raw in asos if start <= t < end]
        vals = [v for v in vals if v is not None]
        rows = [r for r in cli if r.get("valid") == d.isoformat()]
        if not vals or len(rows) != 1:
            problems.append(f"{d}: asos values={len(vals)}, cli rows={len(rows)}")
            continue
        diff = rows[0]["high"] - max(vals)
        notes.append(f"{d.day}:{diff:+.0f}")
        if abs(diff) > 2:
            problems.append(f"{d}: CLI {rows[0]['high']} vs ASOS max {max(vals)}")
    record(name, not problems, "; ".join(problems) or "CLI minus ASOS max per day " + " ".join(notes))


def test5_forecasts():
    for model, max_lead in LEADS.items():
        name = f"5 forecast {model}"
        path = DUMP / f"forecast_{model}.json"
        if not path.exists():
            record(name, False, "file missing")
            continue
        hourly = json.loads(path.read_text()).get("hourly", {})
        problems = []
        expected_cols = ["time"]
        for var in VARIABLES:
            expected_cols.append(var)
            expected_cols += [f"{var}_previous_day{n}" for n in range(1, 8)]
        absent = [c for c in expected_cols if c not in hourly]
        if absent:
            problems.append(f"missing columns {absent[:4]}{'...' if len(absent) > 4 else ''}")
        if len(expected_cols) != 49:
            problems.append("internal: expected 49 columns")
        for lead in range(8):
            col = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
            has_data = any(v is not None for v in hourly.get(col, []))
            want = max_lead is not None and lead <= max_lead
            if has_data != want:
                problems.append(f"lead {lead}: has data={has_data}, expected {want}")
        record(name, not problems, "; ".join(problems) or f"columns complete, leads with data: {'none' if max_lead is None else '0-' + str(max_lead)}")


def test6_kalshi(cli):
    name = "6 Kalshi settled temperature equals CLI high"
    markets = json.loads(MARKETS.read_text())["markets"]
    by_event = {}
    for m in markets:
        by_event.setdefault(m["event_ticker"], set()).add(m["expiration_value"])
    problems = []
    checked = 0
    for event, values in sorted(by_event.items()):
        day = datetime.strptime(event.split("-")[1], "%y%b%d").date()
        if not START <= day <= END:
            continue
        rows = [r for r in cli if r.get("valid") == day.isoformat()]
        if len(rows) != 1:
            problems.append(f"{event}: {len(rows)} CLI rows")
            continue
        checked += 1
        nums = {float(v) for v in values if v not in (None, "")}
        if len(nums) != 1 or nums != {float(rows[0]["high"])}:
            problems.append(f"{event}: Kalshi {sorted(values)} vs CLI {rows[0]['high']}")
    if checked != 7:
        problems.append(f"only {checked} of 7 event days found in markets.json")
    record(name, not problems, "; ".join(problems) or f"{checked} event days match")


def extra_asos_window_bounds(asos):
    name = "EXTRA asos.csv covers end+1 day and has no rows before start"
    times = [t for t, _ in asos]
    lo, hi = min(times), max(times)
    ok = lo >= datetime(2026, 11, 16, tzinfo=timezone.utc) and hi >= datetime(2026, 11, 23, 5, tzinfo=timezone.utc) \
        and hi < datetime(2026, 11, 25, tzinfo=timezone.utc)
    record(name, ok, f"first {lo:%Y-%m-%d %H:%M}, last {hi:%Y-%m-%d %H:%M}")


def extra_forecast_times():
    name = "EXTRA forecast time axis starts 2026-11-16 and ends 2026-11-23, hourly"
    problems = []
    for model in LEADS:
        t = json.loads((DUMP / f"forecast_{model}.json").read_text())["hourly"]["time"]
        if len(t) != 192 or t[0] != "2026-11-16T00:00" or t[-1] != "2026-11-23T23:00":
            problems.append(f"{model}: n={len(t)} {t[0]}..{t[-1]}")
    record(name, not problems, "; ".join(problems) or "192 hours in every file")


def extra_no_volatile_keys():
    name = "EXTRA no generationtime_ms in forecast files"
    bad = [m for m in LEADS if "generationtime_ms" in json.loads((DUMP / f"forecast_{m}.json").read_text())]
    record(name, not bad, f"present in {bad}" if bad else "absent in all")


def main():
    cli = load_cli()
    asos = load_asos()
    test1_station()
    test2_cli(cli)
    test3_asos(asos)
    test4_cli_vs_asos(cli, asos)
    test5_forecasts()
    test6_kalshi(cli)
    extra_asos_window_bounds(asos)
    extra_forecast_times()
    extra_no_volatile_keys()
    failed = results.count(False)
    print(f"{len(results) - failed} passed, {failed} failed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
