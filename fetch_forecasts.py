#!/usr/bin/env python3
"""
fetch_forecasts.py — hourly 2m-temperature forecasts for PAST dates, from many
models, at every lead time. This is the model matrix the density work needs.

    data/processed/forecasts/hourly_temp.parquet   long: one row per
                                                   (city, model, valid_time, lead_day)
    data/processed/forecasts/wide_temp.parquet     pivoted: rows = (city, valid_time),
                                                   cols = model__dLead        [--wide]
    data/processed/forecasts/coverage.csv          what actually arrived

WHY PREVIOUS-RUNS AND NOT THE ARCHIVE API
-----------------------------------------
The archive/reanalysis endpoint tells you what the weather WAS. This endpoint
returns a continuous hourly series where every value was forecast exactly N
days ahead. That distinction is the whole backtest: temperature_2m_previous_day2
at 2026-06-12T18:00Z is what the model said about that hour as of 2026-06-10,
which is information you could actually have traded on.

lead_day=0 is the un-suffixed series — near-analysis, effectively truth-adjacent.
NEVER use it as a feature. It is here only as a diagnostic: lead 1 should track
lead 0 more tightly than lead 7 does. If that ordering fails, the lead semantics
are inverted and everything downstream is wrong.

STILL UNVERIFIED
----------------
The _previous_dayN suffix convention. It is isolated in LEAD_SUFFIX below — if
the API uses different key names, change that one string and nothing else. Run
--dry-run and curl a URL before spending 30 requests.

    python fetch_forecasts.py --start 2026-06-01 --end 2026-06-30 --dry-run
    python fetch_forecasts.py --start 2026-06-01 --end 2026-06-30
    python fetch_forecasts.py --start 2026-06-01 --end 2026-06-30 --wide
    python fetch_forecasts.py --start 2026-06-01 --end 2026-06-30 \
        --cities new_york --models ecmwf_ifs025 --debug

Fail-closed, same convention as weather.py:
    OK       usable rows returned
    MISSING  valid response, no data — the archive genuinely lacks it
    ERROR    transport failure — safe to re-run, results merge
Never conflated, never silently written as data.
"""
from __future__ import annotations

import argparse
import gzip
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

UTC = timezone.utc

# ===========================================================================
# CONFIG
# ===========================================================================

STATIONS = {
    # lat/lon at 4dp: more decimals make api.weather.gov 301-redirect, and
    # Open-Meteo snaps to its own grid anyway.
    "new_york":    dict(station="KNYC", series="KXHIGHNY",  lat=40.7790, lon=-73.9693),
    "los_angeles": dict(station="KLAX", series="KXHIGHLAX", lat=33.9381, lon=-118.3889),
    "miami":       dict(station="KMIA", series="KXHIGHMIA", lat=25.7906, lon=-80.2906),
}

PREVIOUS_RUNS_URL = "https://previous-runs-api.open-meteo.com/v1/forecast"

MODELS = [
    "ecmwf_ifs025",          # ECMWF IFS HRES
    "ecmwf_aifs025",         # ECMWF AIFS (ML) — different error structure
    "gfs_seamless",          # NOAA GFS
    "icon_seamless",         # DWD ICON
    "gem_seamless",          # Environment Canada
    "jma_seamless",          # JMA
    "meteofrance_seamless",  # ARPEGE/AROME
    "ukmo_seamless",         # UK Met Office
    "ncep_nbm_conus",        # NBM — closest retroactive proxy for NWS
    "gfs_hrrr",              # HRRR, short lead, US only
]

VARIABLE    = "temperature_2m"
LEAD_DAYS   = [1, 2, 3, 4, 5, 6, 7]
LEAD_SUFFIX = "_previous_day{n}"        # <-- the unverified bit

TEMPERATURE_UNIT = "fahrenheit"         # Kalshi settles in F; convert once, here
TIMEZONE_PARAM   = "GMT"                # UTC-internal, like weather.py

SLEEP_BETWEEN  = 1.2
RETRY_ATTEMPTS = 4
RETRY_BASE     = 1.5
TIMEOUT        = 90                     # archive queries are slow; do not lower

TRANSIENT  = (urllib.error.URLError, ssl.SSLError, TimeoutError, ConnectionError)
USER_AGENT = "kbt-forecast-research (contact: you@example.com)"

DEBUG = False


def log(msg: str, level: str = "") -> None:
    """flush=True is the point: without it a hang looks like a silent success."""
    prefix = f"[{datetime.now(UTC):%H:%M:%S}] "
    if level:
        prefix += f"{level:<7} "
    print(prefix + msg, flush=True)


# ===========================================================================
# Result record
# ===========================================================================

@dataclass
class FetchResult:
    key:    str
    status: str                     # OK | MISSING | ERROR | DRY
    n_rows: int = 0
    leads:  str = ""
    reason: str | None = None
    url:    str = ""
    frame:  pd.DataFrame | None = field(default=None, repr=False)


# ===========================================================================
# HTTP
# ===========================================================================

def _get(url: str, timeout: int) -> bytes:
    req = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept-Encoding": "gzip"}
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = r.read()
        if r.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        return raw


def get_json(url: str, timeout: int = TIMEOUT, attempts: int = RETRY_ATTEMPTS) -> dict:
    """4xx is NOT retried — it means the request is wrong, not the network."""
    last: Exception | None = None
    for i in range(attempts):
        t0 = time.monotonic()
        log(f"GET attempt {i + 1}/{attempts} timeout={timeout}s", "REQ")
        if DEBUG:
            log(url, "URL")
        try:
            raw = _get(url, timeout)
            log(f"200  {len(raw):,} bytes in {time.monotonic() - t0:.1f}s", "OK")
            return json.loads(raw.decode())
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode()[:400]
            except Exception:
                pass
            log(f"HTTP {e.code} after {time.monotonic() - t0:.1f}s :: {body}", "HTTP")
            if 400 <= e.code < 500:
                raise RuntimeError(f"HTTP {e.code} (not retrying): {body}") from e
            last = e
        except TRANSIENT as e:
            log(f"{type(e).__name__} after {time.monotonic() - t0:.1f}s :: {e}", "NET")
            last = e
        if i < attempts - 1:
            back = RETRY_BASE * (2 ** i)
            log(f"backoff {back:.1f}s", "WAIT")
            time.sleep(back)
    raise RuntimeError(f"unreachable after {attempts} attempts: {last}")


# ===========================================================================
# Build / parse
# ===========================================================================

def build_url(lat: float, lon: float, start: date, end: date, model: str) -> str:
    """One request = one station x one model x whole range x all lead times."""
    hourly = [VARIABLE] + [VARIABLE + LEAD_SUFFIX.format(n=n) for n in LEAD_DAYS]
    params = {
        "latitude":   f"{lat:.4f}",
        "longitude":  f"{lon:.4f}",
        "start_date": start.isoformat(),
        "end_date":   end.isoformat(),
        "hourly":     ",".join(hourly),
        "models":     model,
        "timezone":   TIMEZONE_PARAM,
        "temperature_unit": TEMPERATURE_UNIT,
    }
    return PREVIOUS_RUNS_URL + "?" + urllib.parse.urlencode(params, safe=",")


def parse(payload: dict, city: str, model: str) -> pd.DataFrame:
    """JSON -> long: (city, model, valid_time_utc, lead_day, temp_f).

    All-null columns are dropped: that is how a model tells you it does not
    archive that lead time. Keeping them would poison every downstream mean.
    """
    hourly = payload.get("hourly") or {}
    times = hourly.get("time")
    if not times:
        return pd.DataFrame()

    idx = pd.to_datetime(pd.Series(times), utc=True)
    parts = []
    for col, values in hourly.items():
        if col == "time" or values is None:
            continue
        s = pd.Series(values, dtype="float64")
        if s.notna().sum() == 0:
            continue
        if "_previous_day" in col:
            _, _, tail = col.partition("_previous_day")
            try:
                lead = int(tail)
            except ValueError:
                continue
        else:
            lead = 0
        parts.append(pd.DataFrame({
            "city": city,
            "model": model,
            "valid_time_utc": idx,
            "lead_day": lead,
            "temp_f": s.values,
        }))
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).dropna(subset=["temp_f"])


def fetch_city(city: str, cfg: dict, start: date, end: date,
               models: list[str], dry_run: bool) -> list[FetchResult]:
    out: list[FetchResult] = []
    for model in models:
        url = build_url(cfg["lat"], cfg["lon"], start, end, model)
        key = f"{city}/{model}"
        if dry_run:
            print(f"\n--- {key}\n{url}")
            out.append(FetchResult(key=key, status="DRY", url=url))
            continue

        try:
            payload = get_json(url)
        except Exception as e:
            log(f"{key:32} ERROR   {e}")
            out.append(FetchResult(key=key, status="ERROR", reason=str(e), url=url))
            time.sleep(SLEEP_BETWEEN)
            continue

        # Open-Meteo can return 200 with an error body
        if payload.get("error"):
            reason = str(payload.get("reason", "unspecified"))
            log(f"{key:32} MISSING api error: {reason}")
            out.append(FetchResult(key=key, status="MISSING", reason=reason, url=url))
            time.sleep(SLEEP_BETWEEN)
            continue

        df = parse(payload, city, model)
        if df.empty:
            log(f"{key:32} MISSING no usable values (no archive coverage?)")
            out.append(FetchResult(key=key, status="MISSING",
                                   reason="empty/all-null", url=url))
        else:
            leads = sorted(df.lead_day.unique())
            hours = df.valid_time_utc.nunique()
            log(f"{key:32} OK      rows={len(df):>6} hours={hours} leads={leads}")
            out.append(FetchResult(key=key, status="OK", n_rows=len(df),
                                   leads=str(leads), url=url, frame=df))
        time.sleep(SLEEP_BETWEEN)
    return out


# ===========================================================================
# IO
# ===========================================================================

KEYS = ["city", "model", "valid_time_utc", "lead_day"]


def out_dir(root: str) -> Path:
    p = Path(root) / "processed" / "forecasts"
    p.mkdir(parents=True, exist_ok=True)
    return p


def write_long(root: str, frames: list[pd.DataFrame]) -> Path | None:
    frames = [f for f in frames if f is not None and not f.empty]
    if not frames:
        return None
    df = pd.concat(frames, ignore_index=True)
    path = out_dir(root) / "hourly_temp.parquet"
    if path.exists():                       # merge, never clobber
        df = pd.concat([pd.read_parquet(path), df], ignore_index=True)
    df = df.drop_duplicates(subset=KEYS, keep="last").sort_values(KEYS)
    df.to_parquet(path, index=False)
    return path


def write_wide(root: str) -> Path | None:
    """Pivot to the model matrix: rows (city, valid_time), cols model__dLead.

    This is `m` from the inverse-problem setup — the vector of model opinions
    for one hour. Convenience only; the long file is the source of truth.
    """
    src = out_dir(root) / "hourly_temp.parquet"
    if not src.exists():
        return None
    df = pd.read_parquet(src)
    wide = df.pivot_table(index=["city", "valid_time_utc"],
                          columns=["model", "lead_day"],
                          values="temp_f")
    wide.columns = [f"{m}__d{l}" for m, l in wide.columns]
    path = out_dir(root) / "wide_temp.parquet"
    wide.reset_index().to_parquet(path, index=False)
    return path


def write_coverage(root: str, results: list[FetchResult]) -> Path:
    rows = [{"key": r.key, "status": r.status, "n_rows": r.n_rows,
             "leads": r.leads, "reason": r.reason, "url": r.url} for r in results]
    path = out_dir(root) / "coverage.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


# ===========================================================================
# CLI
# ===========================================================================

def main() -> int:
    global DEBUG
    ap = argparse.ArgumentParser(
        description="Hourly multi-model temperature forecasts for past dates.")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD inclusive")
    ap.add_argument("--end",   required=True, help="YYYY-MM-DD inclusive")
    ap.add_argument("--cities", nargs="*", default=list(STATIONS))
    ap.add_argument("--models", nargs="*", default=MODELS)
    ap.add_argument("--data", default="data")
    ap.add_argument("--wide", action="store_true", help="also write the pivoted matrix")
    ap.add_argument("--dry-run", action="store_true", help="print URLs, fetch nothing")
    ap.add_argument("--debug", action="store_true",
                    help="print URLs and per-attempt timing")
    args = ap.parse_args()
    DEBUG = args.debug

    for c in args.cities:
        if c not in STATIONS:
            ap.error(f"unknown city {c!r}; known: {list(STATIONS)}")

    start, end = date.fromisoformat(args.start), date.fromisoformat(args.end)
    if end < start:
        ap.error("--end is before --start")

    expected_hours = (end - start).days * 24 + 24
    print(f"range {start} .. {end}  ({expected_hours}h expected per model)")
    print(f"cities={args.cities}  models={len(args.models)}  leads={LEAD_DAYS}")
    print(f"variable={VARIABLE}  unit={TEMPERATURE_UNIT}")
    if args.dry_run:
        print("\nDRY RUN — printing URLs only. curl one and check the JSON.")

    results: list[FetchResult] = []
    frames:  list[pd.DataFrame] = []
    for city in args.cities:
        if not args.dry_run:
            print(f"\n[{city}]")
        rs = fetch_city(city, STATIONS[city], start, end, args.models, args.dry_run)
        results.extend(rs)
        frames.extend(r.frame for r in rs if r.frame is not None)

    if args.dry_run:
        return 0

    p = write_long(args.data, frames)
    print(f"\nlong -> {p}" if p else "\nnothing written (no model returned data)")
    if args.wide:
        w = write_wide(args.data)
        print(f"wide -> {w}" if w else "wide: skipped (no long file)")

    cov = write_coverage(args.data, results)
    ok      = sum(r.status == "OK" for r in results)
    missing = sum(r.status == "MISSING" for r in results)
    errors  = sum(r.status == "ERROR" for r in results)
    print(f"\n{ok} OK / {missing} MISSING / {errors} ERROR   coverage -> {cov}")
    if errors:
        print("ERROR   = transport failure. Re-run; results merge, nothing is lost.")
    if missing:
        print("MISSING = archive has nothing. Check the model id spelling, then "
              "drop it from MODELS.")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())