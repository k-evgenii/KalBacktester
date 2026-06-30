#!/usr/bin/env python3
"""
fetch_weather.py — (re)build the official daily-max cache the backtester gates on.

This replaces the old, empty NWS observation JSONs. For every (city, date) in
the range it fetches the CLI product from the IEM archive, parses + validates
the daily MAX (fail-closed), and writes:

    data/processed/weather/<city>.json   ->  { "YYYY-MM-DD": {DailyMax...}, ... }

A date that can't be trusted is stored with status="BLOCKED" and a reason, so
the engine refuses to trade it instead of silently running on a hole.

Run on a machine with internet (the IEM API is external):

    python fetch_weather.py --start 2026-06-01 --end 2026-06-30
    python fetch_weather.py --start 2026-06-01 --end 2026-06-30 --cities new_york
    python fetch_weather.py --start 2026-06-01 --end 2026-06-30 --refresh   # ignore cache

By default it only fetches dates missing (or previously BLOCKED) from the cache.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

from kbt.weather import CITY_CLI, fetch_daily_max


def daterange(start: date, end: date):
    d = start
    while d <= end:
        yield d
        d += timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Build the CLI daily-max cache.")
    ap.add_argument("--start", required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--end",   required=True, help="YYYY-MM-DD (inclusive)")
    ap.add_argument("--cities", nargs="*", default=list(CITY_CLI),
                    help=f"subset of {list(CITY_CLI)} (default: all)")
    ap.add_argument("--data", default="data", help="data root (default: data)")
    ap.add_argument("--refresh", action="store_true",
                    help="re-fetch even dates already OK in the cache")
    ap.add_argument("--sleep", type=float, default=0.6,
                    help="seconds between requests (be kind to IEM)")
    args = ap.parse_args()

    start = date.fromisoformat(args.start)
    end   = date.fromisoformat(args.end)
    if end < start:
        ap.error("--end is before --start")

    out_dir = Path(args.data) / "processed" / "weather"
    out_dir.mkdir(parents=True, exist_ok=True)

    for city in args.cities:
        if city not in CITY_CLI:
            print(f"  ! skip unknown city {city!r}")
            continue

        path = out_dir / f"{city}.json"
        cache: dict[str, dict] = {}
        if path.exists():
            cache = json.loads(path.read_text())

        ok = blocked = skipped = 0
        for d in daterange(start, end):
            iso = d.isoformat()
            prev = cache.get(iso)
            if prev and prev.get("status") == "OK" and not args.refresh:
                skipped += 1
                continue

            rec = fetch_daily_max(city, d)
            cache[iso] = rec.as_dict()
            if rec.status == "OK":
                ok += 1
                tag = f"max={rec.max_f}F @ {rec.max_time_utc} ({rec.time_confidence})"
                if rec.record_flag:
                    tag += " [record]"
            else:
                blocked += 1
                tag = f"BLOCKED: {rec.reason}"
            print(f"  {city:12} {iso}  {tag}")
            time.sleep(args.sleep)

        # write sorted by date for stable diffs
        path.write_text(json.dumps(dict(sorted(cache.items())), indent=2))
        print(f"[{city}] wrote {path}  (+{ok} ok, +{blocked} blocked, {skipped} cached)\n")


if __name__ == "__main__":
    main()
