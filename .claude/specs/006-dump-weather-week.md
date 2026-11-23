# 006 — Dump one week of weather data for one station

Track: A ingestion. Status: APPROVED by the user 2026-11-23 (plan "Weather data: schema,
linking logic and ingestion plan"; first pull = KNYC, 16–22 Nov 2026).

## Goal
Fetch, for one weather station and a range of climate days, the station's metadata, the
official NWS daily climate report values, the hourly station observations, and the hourly
forecasts of ten models at lead days 0–7, and save the raw responses as files. Write a report
that shows coverage per climate day.

## Not built
- No database writes (the weather tables come in unit 007, the loader in 008).
- No derived features (daily forecast highs, spreads, distributions).
- No other stations in this run, no scheduling, no Kalshi data.

## Files
The implementer may create only:
- `api_probe/weather/dump_weather.py` — the fetch script
- everything under `api_probe/weather/dump/` — script output (git-ignored by the lead)

Read for reference: `api_probe/README.md` (sections 2 and 3), `api_probe/weather/REPORT.md`,
`api_probe/weather/samples/`.

## Interfaces

Run from the repo root:
`uv run python api_probe/weather/dump_weather.py --station KNYC --network NY_ASOS --start 2026-11-16 --end 2026-11-22`
All four arguments required; dates are climate days, inclusive. Standard library only
(`urllib`, `json`, `csv`, `argparse`, `datetime`, `zoneinfo`, `time`, `pathlib`). Plain
functions, no classes. `User-Agent: kalbt-probe` on every request, 1 s pause after each
request; on HTTP 429 or 5xx wait 10 s and retry, at most 3 times, then fail.

The IEM id is the station id without its leading `K` (`KNYC` → `NYC`); all stations in scope
are continental US, so they all start with `K`.

Output, under `api_probe/weather/dump/<station>/` (e.g. `dump/KNYC/`):

| File | Request | Content |
|---|---|---|
| `station.json` | `https://mesonet.agron.iastate.edu/geojson/network.py?network=<network>` | the single feature whose `id` equals the IEM id, as returned (fail if absent) |
| `cli_<year>.json` | `https://mesonet.agron.iastate.edu/json/cli.py?station=<station>&year=<year>`, one per calendar year touched by start..end | `results` list as returned, filtered to `start..end` by `valid` |
| `asos.csv` | `https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=<iem_id>&data=tmpf&data=dwpf&data=relh&data=sknt&data=p01i&data=metar&year1=..&month1=..&day1=<start>&year2=..&month2=..&day2=<end + 2 days>&tz=Etc/UTC&format=onlycomma&report_type=3&report_type=4` | the CSV text as returned (`day2` is exclusive, so this covers through end+1 23:59 UTC) |
| `forecast_<model>.json` | Open-Meteo Previous Runs, one request per model (below) | response as returned, minus the key `generationtime_ms` |
| `_report.json` | — | coverage report (below) |

All JSON is written with `json.dumps(obj, indent=1, sort_keys=True)`. Drop the keys that change
on every call (`generationtime_ms` in Open-Meteo responses, `generated_at` in IEM responses) so
two runs give identical files.

Models (all ten; `ecmwf_aifs025` is expected to be empty for 2026 and stays in the list for the
later backfill of 2024):
`ecmwf_ifs025, ecmwf_aifs025, gfs_seamless, icon_seamless, gem_seamless, jma_seamless,
meteofrance_seamless, ukmo_seamless, ncep_nbm_conus, gfs_hrrr`

Forecast request (tested by the lead, returns 48 columns):
```
https://previous-runs-api.open-meteo.com/v1/forecast
  ?latitude=<station lat>&longitude=<station lon>        (from station.json geometry)
  &start_date=<start>&end_date=<end + 1 day>
  &hourly=<for each of temperature_2m, dew_point_2m, cloud_cover, wind_speed_10m,
           precipitation, shortwave_radiation: the name itself and name_previous_day1 … _previous_day7>
  &models=<model>&timezone=GMT
  &temperature_unit=fahrenheit&wind_speed_unit=mph&precipitation_unit=inch
```
Build it with `urllib.parse.urlencode(params, safe=",")`.

## Hard parts (use as written)

The climate day is local **standard** time all year (NWS convention), so it is a fixed UTC
window per station:
```python
def std_offset(tz_name, year):
    """UTC offset of local standard time, e.g. -5 h for America/New_York."""
    return ZoneInfo(tz_name).utcoffset(datetime(year, 1, 15))

def climate_day_window(day, tz_name):
    """[start, end) in UTC for climate day `day`."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc) - std_offset(tz_name, day.year)
    return start, start + timedelta(hours=24)
```
For New York, climate day 2026-11-22 is 2026-11-22 05:00 UTC to 2026-11-23 05:00 UTC.

Report (`_report.json`), descriptive only, never fails the run:
```
{"station": "KNYC", "start", "end", "tz_name", "std_offset_hours": -5,
 "requested_lat_lon": [lat, lon],            # from station.json
 "forecast_grid_lat_lon": {model: [lat, lon]},   # what Open-Meteo snapped to
 "days": {"2026-11-22": {
     "window_utc": [start, end],
     "cli": {"high": 65, "high_time": "247 PM", "low": .., "product": ".."} or null,
     "asos_reports": n, "asos_hours_with_report": n (of 24), "asos_max_tmpf": x or null,
     "forecast_hours": {model: {lead: n non-null temperature hours in the window}}
 }},
 "requests": n}
```
ASOS `tmpf` is `M` when missing; treat it as no value, never as 0.

## Agreed tests
Decided by the user and the lead. The qa-tester writes `api_probe/weather/check_weather_dump.py`
from this list, the Interfaces section and the files, without reading `dump_weather.py` first.
Prints `[PASS]` / `[FAIL]` per check, exits 0 only if all pass. Run from the repo root with
`uv run python api_probe/weather/check_weather_dump.py`.

1. `station.json` is the KNYC feature: id `NYC`, time zone `America/New_York`, coordinates
   within 0.01° of (40.779, −73.969).
2. Every climate day 2026-11-16 … 2026-11-22 has exactly one CLI row with an integer `high`,
   a `high_time`, and a `product` id; the product's issuance time (its first 12 digits,
   `YYYYMMDDHHMM`, UTC) is at or after the end of that climate day's window (a final report,
   not the same-day preliminary). If this fails because IEM gives preliminary rows, report it
   with the evidence; do not loosen it.
3. Every climate day has at least one ASOS report in each of its 24 UTC hours, and every
   `tmpf` is a number or `M`.
4. For every climate day, the CLI `high` is within 2 °F of the highest ASOS `tmpf` in that
   day's window.
5. Forecast leads per model, over the whole requested range, for `temperature_2m` (lead 0) and
   `temperature_2m_previous_dayN`: non-null data exactly for
   `ecmwf_ifs025, gfs_seamless, gem_seamless, jma_seamless, ncep_nbm_conus` → leads 0–7;
   `icon_seamless, ukmo_seamless` → 0–6; `meteofrance_seamless` → 0–3; `gfs_hrrr` → 0–1;
   `ecmwf_aifs025` → none. Every forecast file has all 48 requested columns plus `time`.
6. The Kalshi settled temperature for each event day (from
   `api_probe/kalshi/KXHIGHNY/markets.json`, `expiration_value`) equals that day's CLI `high`;
   any difference is listed and fails the check.
7. Idempotence: the lead runs the dump twice and compares the folders (not in the script).

## Test files
- `api_probe/weather/check_weather_dump.py`

## Legacy reference
`legacy/fetch_forecasts.py` (`build_url`, `parse`), `legacy/kbt/weather.py` (CLI, standard-time
offsets). Do not copy their parsing; this unit stores raw responses.

## Done when
- the run command above exits 0 (about 13 requests)
- `uv run python api_probe/weather/check_weather_dump.py` exits 0 with every check `[PASS]`

If anything here is unclear or looks wrong: stop and report, do not guess.
