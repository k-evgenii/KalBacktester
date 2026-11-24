# B018 — Weather dump QA gate: same key, different value

Track: B. Design approved by Track A (lead) on 2026-11-24, with amendments (all included below).
Track A granted Track B the single path `ingest/qa_weather_dump.py`.

## Goal
`ingest/load_weather_dump.py` inserts with `ON CONFLICT DO NOTHING`, so a second, different
value for a key that already exists is dropped silently and the first value wins. This gate
builds exactly the rows the loader would insert and BLOCKs on any key that carries two
different values, either within the dump or between the dump and the database.

## Not built
No choosing which value wins, no repair, no loader change (Track A hooks the gate into the
loader after merge), no range checks (the schema CHECKs cover them), no database writes.

## Files
- `ingest/qa_weather_dump.py` (new). Everything else is off limits, especially
  `ingest/load_weather_dump.py`, which is only imported.

## Interfaces

Reused read-only from `ingest.load_weather_dump` (do not copy or reimplement):
`cli_rows(station, dump_dir)`, `kalshi_rows(cur, markets_file)`,
`observation_rows(station, dump_dir)`, `forecast_rows(station, model, forecast_file)`,
`WEATHER_DIR`, `MARKETS_FILE`. These return tuples in the column order of the loader's
INSERTs. Note that `kalshi_rows` calls `sys.exit` on bad input; let that propagate.

The four row sets. The key is the table's primary key, and **every other column is compared**
(no exceptions):

| set | table | columns, in tuple order | key positions |
|---|---|---|---|
| `nws_cli` | official_daily | station_id, climate_date, source, high_f, high_time_lst, low_f, issued_at, reference | 0, 1, 2 |
| `kalshi` | official_daily | same | 0, 1, 2 |
| `observation` | observation_hourly | station_id, observed_at, temp_f, dewpoint_f, rel_humidity_pct, wind_speed_kt, precip_in, metar | 0, 1 |
| `forecast` | forecast_hourly | station_id, model, valid_time, lead_day, temp_f, dewpoint_f, cloud_cover_pct, wind_speed_mph, precip_in, shortwave_wm2 | 0, 1, 2, 3 |

The forecast rows are all `forecast_*.json` files in the dump folder, sorted, with
`model = file.stem.removeprefix("forecast_")`, exactly as the loader does.

```python
def dump_row_sets(cur, station: str, dump_dir: Path, markets_file: Path) -> dict[str, list[tuple]]
def within_dump_conflicts(rows: list[tuple], n_key: int) -> tuple[int, list[dict]]
    # (number of exact duplicate rows, conflicts)
def db_comparison(cur, set_name: str, rows: list[tuple], n_key: int) -> dict
    # {"new": int, "equal": int, "conflicts": [dict]}
def run_checks(conn, station, dump_dir, markets_file) -> dict   # the report
```

Command line (SELECT only):
`uv run python -m ingest.qa_weather_dump --station KNYC [--dump-dir PATH] [--markets PATH] [--db NAME]`
The defaults are `WEATHER_DIR / station`, `MARKETS_FILE` and `kalbt`, the same as the loader.
It prints one line per check (`[PASS]`/`[BLOCK]` with id, name and counts) and a verdict line. It
writes the report as JSON (`default=str`) to `<dump_dir>/_weather_qa_report.json`. Exit 0 on
PASS, 1 on BLOCK.

Report:
```
{"station", "dump_dir", "markets_file", "checked_at", "verdict": "PASS" | "BLOCK",
 "checks": [{"id", "name", "status": "pass" | "block", "detail", "examples": [...max 5]}]}
```
There are eight checks, in this order:
- `1_nws_cli_within`, `1_kalshi_within`, `1_observation_within`, `1_forecast_within`
- `2_nws_cli_db`, `2_kalshi_db`, `2_observation_db`, `2_forecast_db`

Detail strings:
- within: `"<n> rows, <d> exact duplicates, <c> conflicting keys"`
- db: `"<n> rows: <new> new, <equal> equal, <c> conflicting"`

Examples: `{"key": [...], "values": [[...], [...]]}`. Within the dump, `values` lists the first
two differing value tuples. Against the database, `values` is `[dump_values, db_values]`.

## Hard parts (use as written)

Comparison: split each tuple into `key = row[:n_key]` and `values = row[n_key:]`. Tuples compare
with plain `==`. That works because:
- Decimal("60.0") == Decimal("60");
- None == None, text == text;
- aware datetimes compare by instant, whatever the session time zone.

No normalisation beyond that.

Within the dump:
```python
first = {}
duplicates = 0
conflicts = {}
for row in rows:
    key, values = row[:n_key], row[n_key:]
    if key not in first:
        first[key] = values
    elif first[key] == values:
        duplicates += 1
    elif key not in conflicts:
        conflicts[key] = [first[key], values]
```

Against the database: read only the database rows inside the dump's key range, then compare in
Python. In each query, `station` is the dump's station (row[0]). The bounds are the min and max
of the second key column (and of the third for forecast) over the dump rows:

```sql
-- nws_cli / kalshi: source = 'nws_cli' or 'kalshi'
SELECT station_id, climate_date, source, high_f, high_time_lst, low_f, issued_at, reference
FROM official_daily
WHERE station_id = %(station)s AND source = %(source)s
  AND climate_date BETWEEN %(lo)s AND %(hi)s
-- observation
SELECT station_id, observed_at, temp_f, dewpoint_f, rel_humidity_pct, wind_speed_kt, precip_in, metar
FROM observation_hourly
WHERE station_id = %(station)s AND observed_at BETWEEN %(lo)s AND %(hi)s
-- forecast (bounds on valid_time)
SELECT station_id, model, valid_time, lead_day, temp_f, dewpoint_f, cloud_cover_pct,
       wind_speed_mph, precip_in, shortwave_wm2
FROM forecast_hourly
WHERE station_id = %(station)s AND valid_time BETWEEN %(lo)s AND %(hi)s
```
Build `db = {row[:n_key]: row[n_key:]}`. For each distinct dump key (first value wins, as in the
loader): if it is not in `db`, it is new; if it equals the db values, it is equal; otherwise it
is a conflict. An empty row set is a pass with detail `"0 rows"`, and no query is run for it.

The `kalshi` row set needs the cursor, because `kalshi_rows` looks up series → station. Its
station can differ from `--station` only if the markets file belongs to another series. The
gate does not check that; it compares whatever `kalshi_rows` returns.

## Agreed tests
`tests/ingest/check_qa_weather_dump.py`: a plain script with `[PASS]`/`[FAIL]` lines, exit 1 on
any failure, run with `uv run python -m tests.ingest.check_qa_weather_dump`.
- It uses a throwaway database `kalbt_b018_test` built from `db/schema.sql`, with a minimal
  fixture: a station `KTST` (offset -5), its underlying and series `KXHIGHTST`.
- It writes hand-made mini dumps in a temp dir, in the loader's file formats: `cli_2026.json`,
  `asos.csv`, one or two `forecast_<model>.json` with `temperature_2m...` and `_previous_dayN`
  columns, and a `markets.json` for `KXHIGHTST`.
- It drops the database in `finally`.
- Expected values are written by hand.

1. A clean dump against an empty database: all 8 checks pass and the verdict is PASS. The db
   checks report every row as new.
2. Within the dump, one conflict per set, and each gives BLOCK on its own `1_*` check:
   - `nws_cli`: two CLI products for the same day with different `high`;
   - `kalshi`: the loader's `kalshi_rows` exits when one event's markets disagree, so this case
     is checked as "the gate exits non-zero with the loader's message" instead of a `1_kalshi`
     BLOCK;
   - `observation`: two `asos.csv` lines with the same `valid` time and different `tmpf`;
   - `forecast`: two forecast files that map to the same model. That cannot happen, because the
     file name is the model. So use one file whose `hourly.time` lists the same hour twice, with
     different temperatures.
3. An exact duplicate row within the dump (same key, same values) passes, and it is counted in
   the detail as an exact duplicate.
4. Against the database, one conflict per set, and each gives BLOCK on its `2_*` check with
   `[dump_values, db_values]` in the examples. The fixture inserts rows that differ from the dump
   in exactly one column. For official_daily, use `issued_at` only: same high, different issue
   time, BLOCK.
5. Re-running an already-loaded dump: insert exactly the rows the loader would insert, then run
   the gate. Every db check reports all rows equal and the verdict is PASS.
6. `60.0` in the database against `60` in the dump counts as equal.
7. Two weekly dumps overlapping by one day with equal values: load dump A, then gate dump B,
   which shares the last day with A. The result is PASS, with the shared rows counted as equal
   and the rest as new.
8. The report JSON is written to the dump folder, with eight checks in the stated order.
9. The live database `kalbt` row counts of official_daily, observation_hourly and forecast_hourly
   are unchanged after the script.
10. Real dumps, read-only against the live `kalbt`. Each of the three runs gives verdict PASS,
    no conflicts, and every db row counted as equal (no new rows, since all three weeks are
    loaded):
    - `--dump-dir /home/kondr/KalBacktester/api_probe/weather/dump/KNYC --markets /home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY/markets.json`
    - `--dump-dir /home/kondr/KalBacktester/api_probe/weather/dump_2026-06-01_2026-06-07/KNYC --markets /home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-06-01_2026-06-07/markets.json`
    - `--dump-dir /home/kondr/KalBacktester/api_probe/weather/dump_2026-08-12_2026-08-18/KNYC --markets /home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-08-12_2026-08-18/markets.json`

    The dumps live in the main checkout, not in this worktree, so the paths are absolute. If a
    real run is not all equal, report the counts and examples. Do not change the gate to make it
    pass; the lead decides.

    Note that running the CLI on a real dump writes `_weather_qa_report.json` into that dump
    folder in the main checkout. That is allowed; it is the gate's report file.

## Test files
- `tests/ingest/check_qa_weather_dump.py` (new; Track A owns `tests/ingest/`, and this one new file is agreed with Track A)

## Legacy reference
none (pattern: `ingest/qa_kxhighny_dump.py`, Track A's dump gate)

## Done when
`uv run python -m tests.ingest.check_qa_weather_dump` all PASS, including the three real dumps;
reviewer APPROVE; nothing written to any database.

If anything here is unclear or looks wrong: stop and report, do not guess.
