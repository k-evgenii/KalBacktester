"""Dump one station's weather data for a range of climate days (spec 006).

Fetches station metadata, NWS daily climate (CLI) rows, hourly ASOS observations and
hourly forecasts of ten models at lead days 0-7, saves the raw responses under
api_probe/weather/dump/<station>/ (or --out) and writes a coverage report.

Run from the repo root:
  uv run python api_probe/weather/dump_weather.py --station KNYC --network NY_ASOS \
      --start 2026-11-16 --end 2026-11-22
"""

import argparse
import csv
import io
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

MODELS = [
    "ecmwf_ifs025",
    "ecmwf_aifs025",
    "gfs_seamless",
    "icon_seamless",
    "gem_seamless",
    "jma_seamless",
    "meteofrance_seamless",
    "ukmo_seamless",
    "ncep_nbm_conus",
    "gfs_hrrr",
]
VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "cloud_cover",
    "wind_speed_10m",
    "precipitation",
    "shortwave_radiation",
]
LEADS = range(8)

request_count = 0


def std_offset(tz_name, year):
    """UTC offset of local standard time, e.g. -5 h for America/New_York."""
    return ZoneInfo(tz_name).utcoffset(datetime(year, 1, 15))


def climate_day_window(day, tz_name):
    """[start, end) in UTC for climate day `day`."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - std_offset(tz_name, day.year)
    return start, start + timedelta(hours=24)


def fetch(url):
    """GET a URL and return the body text. Retry on 429 / 5xx, at most 3 times."""
    global request_count
    retries = 0
    while True:
        request = urllib.request.Request(url, headers={"User-Agent": "kalbt-probe"})
        request_count += 1
        try:
            with urllib.request.urlopen(request, timeout=120) as response:
                body = response.read().decode("utf-8")
            time.sleep(1)
            return body
        except urllib.error.HTTPError as error:
            time.sleep(1)
            if (error.code == 429 or error.code >= 500) and retries < 3:
                retries += 1
                print(f"HTTP {error.code}, waiting 10 s (retry {retries})")
                time.sleep(10)
                continue
            raise


def write_json(path, obj):
    path.write_text(json.dumps(obj, indent=1, sort_keys=True))


def fetch_station(network, iem_id):
    url = "https://mesonet.agron.iastate.edu/geojson/network.py?" + urllib.parse.urlencode({"network": network})
    features = json.loads(fetch(url))["features"]
    for feature in features:
        if feature["id"] == iem_id:
            return feature
    raise SystemExit(f"station {iem_id} not found in network {network}")


def fetch_cli(station, start, end, out_dir):
    """Save cli_<year>.json for each year touched; return the CLI rows in start..end."""
    rows = []
    for year in range(start.year, end.year + 1):
        url = "https://mesonet.agron.iastate.edu/json/cli.py?" + urllib.parse.urlencode(
            {"station": station, "year": year}
        )
        results = json.loads(fetch(url))["results"]
        in_range = [r for r in results if start.isoformat() <= r["valid"] <= end.isoformat()]
        write_json(out_dir / f"cli_{year}.json", in_range)
        rows += in_range
    return rows


def fetch_asos(iem_id, start, end, out_dir):
    """Save asos.csv and return its rows as dicts."""
    stop = end + timedelta(days=2)  # day2 is exclusive
    params = [
        ("station", iem_id),
        ("data", "tmpf"),
        ("data", "dwpf"),
        ("data", "relh"),
        ("data", "sknt"),
        ("data", "p01i"),
        ("data", "metar"),
        ("year1", start.year),
        ("month1", start.month),
        ("day1", start.day),
        ("year2", stop.year),
        ("month2", stop.month),
        ("day2", stop.day),
        ("tz", "Etc/UTC"),
        ("format", "onlycomma"),
        ("report_type", 3),
        ("report_type", 4),
    ]
    url = "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?" + urllib.parse.urlencode(params)
    text = fetch(url)
    (out_dir / "asos.csv").write_text(text)
    return list(csv.DictReader(io.StringIO(text)))


def fetch_forecast(model, lat, lon, start, end, out_dir):
    """Save forecast_<model>.json and return the parsed response."""
    columns = []
    for name in VARIABLES:
        columns.append(name)
        columns += [f"{name}_previous_day{lead}" for lead in range(1, 8)]
    params = {
        "latitude": lat,
        "longitude": lon,
        "start_date": start.isoformat(),
        "end_date": (end + timedelta(days=1)).isoformat(),
        "hourly": ",".join(columns),
        "models": model,
        "timezone": "GMT",
        "temperature_unit": "fahrenheit",
        "wind_speed_unit": "mph",
        "precipitation_unit": "inch",
    }
    url = "https://previous-runs-api.open-meteo.com/v1/forecast?" + urllib.parse.urlencode(params, safe=",")
    data = json.loads(fetch(url))
    data.pop("generationtime_ms", None)
    write_json(out_dir / f"forecast_{model}.json", data)
    return data


def parse_utc(text):
    """Parse 'YYYY-MM-DD HH:MM' or 'YYYY-MM-DDTHH:MM' as UTC."""
    return datetime.fromisoformat(text.replace(" ", "T")).replace(tzinfo=timezone.utc)


def day_report(day, tz_name, cli_rows, asos_rows, forecasts):
    window_start, window_end = climate_day_window(day, tz_name)

    cli = None
    for row in cli_rows:
        if row["valid"] == day.isoformat():
            cli = {
                "high": row["high"],
                "high_time": row["high_time"],
                "low": row["low"],
                "product": row["product"],
            }

    reports = 0
    hours = set()
    max_tmpf = None
    for row in asos_rows:
        valid = parse_utc(row["valid"])
        if not window_start <= valid < window_end:
            continue
        reports += 1
        hours.add(valid.replace(minute=0))
        if row["tmpf"] != "M":  # M means missing, never 0
            tmpf = float(row["tmpf"])
            if max_tmpf is None or tmpf > max_tmpf:
                max_tmpf = tmpf

    forecast_hours = {}
    for model, data in forecasts.items():
        hourly = data["hourly"]
        times = [parse_utc(t) for t in hourly["time"]]
        counts = {}
        for lead in LEADS:
            column = "temperature_2m" if lead == 0 else f"temperature_2m_previous_day{lead}"
            values = hourly.get(column, [])
            counts[lead] = sum(
                1
                for t, v in zip(times, values)
                if window_start <= t < window_end and v is not None
            )
        forecast_hours[model] = counts

    return {
        "window_utc": [window_start.isoformat(), window_end.isoformat()],
        "cli": cli,
        "asos_reports": reports,
        "asos_hours_with_report": len(hours),
        "asos_max_tmpf": max_tmpf,
        "forecast_hours": forecast_hours,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--station", required=True)
    parser.add_argument("--network", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--out", type=Path, help="output folder (default dump/<station>/ next to this script)")
    args = parser.parse_args()

    iem_id = args.station[1:]  # KNYC -> NYC
    out_dir = args.out or Path(__file__).parent / "dump" / args.station
    out_dir.mkdir(parents=True, exist_ok=True)

    feature = fetch_station(args.network, iem_id)
    write_json(out_dir / "station.json", feature)
    lon, lat = feature["geometry"]["coordinates"]
    tz_name = feature["properties"]["tzname"]

    cli_rows = fetch_cli(args.station, args.start, args.end, out_dir)
    asos_rows = fetch_asos(iem_id, args.start, args.end, out_dir)

    forecasts = {}
    for model in MODELS:
        forecasts[model] = fetch_forecast(model, lat, lon, args.start, args.end, out_dir)
        print(f"fetched forecast {model}")

    days = {}
    day = args.start
    while day <= args.end:
        days[day.isoformat()] = day_report(day, tz_name, cli_rows, asos_rows, forecasts)
        day += timedelta(days=1)

    report = {
        "station": args.station,
        "start": args.start.isoformat(),
        "end": args.end.isoformat(),
        "tz_name": tz_name,
        "std_offset_hours": std_offset(tz_name, args.start.year).total_seconds() / 3600,
        "requested_lat_lon": [lat, lon],
        "forecast_grid_lat_lon": {m: [d["latitude"], d["longitude"]] for m, d in forecasts.items()},
        "days": days,
        "requests": request_count,
    }
    write_json(out_dir / "_report.json", report)
    print(f"wrote {out_dir}, {request_count} requests")


if __name__ == "__main__":
    main()
