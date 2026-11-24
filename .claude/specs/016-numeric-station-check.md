# 016 — Numbers confirm the station; weather for the June and August weeks

Track: A ingestion. Status: APPROVED by the user 2026-11-24 (plan "Rules-text wording",
units 014–016).

## Goal
Before the weather loader inserts anything, compare Kalshi's settled temperature with the NWS
climate-report high for the same station and day: one disagreeing day is a warning, a pattern of
disagreement blocks the load as a likely wrong station. Then fetch and load the weather for the
two weeks loaded in unit 015 (1–7 June and 12–18 August 2026), so all 21 New York event days have
both market and weather data.

## Not built
- No other stations, no derived features, no change to the market side.

## Files
The implementer may change only:
- `api_probe/weather/dump_weather.py` — optional `--out` folder (default unchanged)
- `ingest/load_weather_dump.py` — optional `--dump-dir` and `--markets`, the numeric check
- `.gitignore` — the weather dump pattern below
and create the weather dump folders listed under "Real runs" (git-ignored).

## Hard parts (use as written)

Rule (user decision 2026-11-24: "a single mismatched day warns; more than 2 % of days blocks",
made consistent for short dumps by the lead):
```python
def numeric_verdict(n_days, n_mismatches):
    """'ok', 'warn' or 'block'. Block needs at least 2 mismatched days AND more than 2 % of days."""
    if n_mismatches == 0:
        return "ok"
    if n_mismatches >= 2 and n_mismatches > 0.02 * n_days:
        return "block"
    return "warn"
```
In `ingest/load_weather_dump.py`, before the transaction opens:
```
days compared = every event day that has both a Kalshi value (from --markets, expiration_value,
                all markets of the event equal) and a CLI row (from the dump's cli_<year>.json)
mismatch      = Decimal(expiration_value) != Decimal(str(cli high))
print one line per mismatch: "WARN station check: <day> kalshi <x> nws <y>"
print "station check: <n> days compared, <m> mismatches -> <verdict>"
verdict 'block' -> print "station check: BLOCK, nothing inserted" and exit 1 before connecting for writes
```
Arguments: `--dump-dir` (default `api_probe/weather/dump/<station>/`) and `--markets` (default
`api_probe/kalshi/KXHIGHNY/markets.json`); `--station` stays required.

Weather dump: `--out` optional; when given, files go there instead of
`api_probe/weather/dump/<station>/`.

`.gitignore`: replace `api_probe/weather/dump/` with `api_probe/weather/dump*/`.

## Real runs (implementer; live APIs and live database; approved)
1. `uv run python api_probe/weather/dump_weather.py --station KNYC --network NY_ASOS --start 2026-06-01 --end 2026-06-07 --out api_probe/weather/dump_2026-06-01_2026-06-07/KNYC`
2. same for `--start 2026-08-12 --end 2026-08-18 --out api_probe/weather/dump_2026-08-12_2026-08-18/KNYC`
3. Load each with `--dump-dir <that folder> --markets api_probe/kalshi/KXHIGHNY_<same range>/markets.json`;
   run each load a second time (0 rows inserted). The November load is rerun with defaults (0 rows).
If the station check warns or blocks on a real week, stop and report the days and values: do not
change data or the rule.

## Agreed tests
`qa-tester` writes `tests/ingest/check_station_numbers.py` and scopes the existing
`tests/ingest/check_load_weather_dump.py` to its own (November) dump, as unit 015 did for the market
load test (count and compare only rows for the November climate days; weaken nothing).
1. `numeric_verdict`: (7, 0) ok; (7, 1) warn; (7, 2) block; (365, 7) warn; (365, 8) block;
   (100, 2) warn; (100, 3) block. (Importing this one function is allowed: it is the unit under test.)
2. Real weeks: for June, August and November, Kalshi's value equals the NWS high on every day
   compared (computed by the test from the dumps), and the loader prints `-> ok` for each.
3. Block path: a temporary copy of the November weather dump with the CLI high changed on 2 of
   the 7 days → the loader (run with `--dump-dir <copy>`) exits 1, prints the BLOCK line and both
   WARN lines, and no table's row count changes. (Never run the loader on a tampered copy that
   would only warn: it would insert tampered data into the live database.)
4. Live database after the loads: `official_daily` has 21 `nws_cli` and 21 `kalshi` rows for KNYC,
   one per event day; for all 21 events the official high (kalshi) falls in the winning market's
   strike range; kalshi and nws_cli highs are equal on every day.
5. `observation_hourly` and `forecast_hourly` have rows on all 21 climate days (at least 24
   observations per day; every model with data has at least one lead covering each day).

## Test files
- `tests/ingest/check_station_numbers.py`
- `tests/ingest/check_load_weather_dump.py`

## Legacy reference
none

## Done when
- both new weather dumps exist and are loaded; reruns insert nothing
- `uv run python -m tests.ingest.check_station_numbers` and
  `uv run python -m tests.ingest.check_load_weather_dump` exit 0
- all other ingest suites still exit 0

If anything here is unclear or looks wrong: stop and report, do not guess.
