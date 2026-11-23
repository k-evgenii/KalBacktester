# 009 — Load the one-week weather dump into the weather tables

Track: A ingestion. Status: APPROVED by the user 2026-11-23.

## Goal
Read the KNYC weather dump (unit 006) and the Kalshi market dump (unit 002) and insert into
`official_daily` (both sources), `observation_hourly` and `forecast_hourly`. Running it twice
must add nothing the second time.

## Not built
- No derived features (daily forecast highs, spreads, distributions).
- No other stations; no fetching; no scheduling.
- No "same key, different values" check: that belongs to Track B's QA gate (unit 004), which
  will be extended to the weather tables once it exists. Until then a rerun silently keeps the
  first values, as the market loader does.

## Files
The implementer may create only:
- `ingest/load_weather_dump.py`

Read for reference: `db/schema.sql` (weather section), `ingest/load_kxhighny_dump.py` (same
style), the dump files under `api_probe/weather/dump/KNYC/` and
`api_probe/kalshi/KXHIGHNY/markets.json`.

## Interfaces

Run from the repo root: `uv run python -m ingest.load_weather_dump --station KNYC`
(`--station` required; reads `api_probe/weather/dump/<station>/` and
`api_probe/kalshi/KXHIGHNY/markets.json`). Uses `db.connect.connect`. One transaction, commit
once at the end; every insert ends with `ON CONFLICT DO NOTHING`; prints rows inserted per table
and source; exits 0. Fails (non-zero, nothing inserted) if the station is not in `station`.

## Hard parts (use as written)

Mapping. Numbers as `Decimal(str)`, never float. In `asos.csv` the string `M` means missing in
any column → NULL.

| Target | Source | Values |
|---|---|---|
| `official_daily`, `source='nws_cli'` | every row of every `cli_<year>.json` | `climate_date` ← `valid`; `high_f` ← `high`; `high_time_lst` ← `high_time`; `low_f` ← `low`; `reference` ← `product`; `issued_at` ← first 12 characters of `product` parsed as `%Y%m%d%H%M` in UTC |
| `official_daily`, `source='kalshi'` | `markets.json`, one row per `event_ticker` | `climate_date` ← date from the ticker (`%y%b%d`); `high_f` ← `expiration_value` (all markets of an event carry the same value; if they differ, fail); `issued_at` ← the earliest `settlement_ts` of the event's markets; `reference` ← the event ticker; `high_time_lst`, `low_f` NULL. Station = the `station_id` of the series' underlying in the database |
| `observation_hourly` | every data row of `asos.csv` | `observed_at` ← `valid` (`%Y-%m-%d %H:%M`, UTC); `temp_f` ← `tmpf`; `dewpoint_f` ← `dwpf`; `rel_humidity_pct` ← `relh`; `wind_speed_kt` ← `sknt`; `precip_in` ← `p01i`; `metar` ← `metar` |
| `forecast_hourly` | every `forecast_<model>.json` | for each index `i` of `hourly.time` and each lead `N` in 0..7: `valid_time` ← `time[i]` (UTC); `lead_day` ← N; each variable from column `<var>` when N = 0, else `<var>_previous_day<N>`. **Skip the row when all six values are null** |

Variable → column: `temperature_2m` → `temp_f`, `dew_point_2m` → `dewpoint_f`,
`cloud_cover` → `cloud_cover_pct`, `wind_speed_10m` → `wind_speed_mph`,
`precipitation` → `precip_in`, `shortwave_radiation` → `shortwave_wm2`.
The model name is the file name between `forecast_` and `.json`. Use `executemany` per file.

## Agreed tests
Note: forecast values are stored exactly as returned, including negative wind, precipitation
and radiation (see spec 007, "Change after approval"). No clipping in the loader.

`tests/ingest/check_load_weather_dump.py`, run with
`uv run python -m tests.ingest.check_load_weather_dump`; expectations recomputed from the dump
files, never from the loader's code.
1. Row counts: 7 `nws_cli` and 7 `kalshi` rows for KNYC; `observation_hourly` rows equal the
   data rows of `asos.csv`; `forecast_hourly` rows equal the (model, time, lead) combinations
   with at least one non-null value.
2. Every `nws_cli` row equals its CLI row (high, time, low, product, issuance time).
3. Every `observation_hourly` row equals its CSV row, with each `M` stored as NULL.
4. Every `forecast_hourly` row equals the file value for its model, time and lead; `ecmwf_aifs025`
   has no rows; the leads present per model match the coverage in spec 006 test 5.
5. **End-to-end link:** for each of the 7 events, joining `event → series → underlying → station
   → official_daily (kalshi)` on `climate_date = event_date`, the high falls inside the strike
   range of the event's one market with `result = 'yes'` (greater: high > floor; less: high <
   cap; between: floor ≤ high ≤ cap). Expected 7 of 7. The `kalshi` and `nws_cli` highs are
   equal on all 7 days.
6. Running the loader a second time reports 0 rows inserted everywhere and leaves counts unchanged.

## Test files
- `tests/ingest/check_load_weather_dump.py`

## Legacy reference
none

## Done when
- `uv run python -m ingest.load_weather_dump --station KNYC` exits 0
- `uv run python -m tests.ingest.check_load_weather_dump` exits 0 with every check `[PASS]`

If anything here is unclear or looks wrong: stop and report, do not guess.
