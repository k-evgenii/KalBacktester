# A021 — Backfill runner: 13 weeks for every seeded city

Track: A. Status: APPROVED (overnight plan, PLAYBOOK §7: "all US cities, last 3 months, 1-minute
bars, no trades"; user 2026-11-24).

## Goal
One resumable script that, for every seeded series and every Monday–Sunday week from 2026-08-24 to
2026-11-22 (13 weeks), dumps markets (no trades), loads them through the QA gate, then dumps and
loads the station's weather through the numeric station check and the weather QA gate. City-weeks
already in the database are skipped, so it can be stopped and restarted at any time.

## Not built
- No new ingest logic: the runner only calls the existing scripts as subprocesses
  (`api_probe/kalshi/dump_kxhighny.py`, `ingest.load_kxhighny_dump`, `api_probe/weather/dump_weather.py`,
  `ingest.load_weather_dump`) and `db.backup`. No parallel downloads.
- No backtest runs (Track B's, after its B019; the runner only reports what it loaded).

## Files
- `ingest/backfill.py` (new)
- `.gitignore`: `ingest/backfill_log.jsonl`

## Interfaces
`uv run python -m ingest.backfill [--start 2026-08-24] [--end 2026-11-22] [--series S ...] [--dry]`
- weeks: Monday `start` + 7k … Sunday, up to `end` (`start` must be a Monday, `end` a Sunday);
- cities: `SELECT se.ticker, st.station_id, st.iem_network FROM series se JOIN underlying u USING
  (underlying_id) JOIN station st ON st.station_id = u.station_id ORDER BY se.ticker`, optionally
  filtered by `--series`;
- `--dry`: print the plan (city, week, `skip` or `todo`) and the totals; touch nothing.

## Hard parts (use as written)
```
done(series, station, monday, sunday) =
    (SELECT count(*) FROM event WHERE series_ticker = series AND event_date BETWEEN monday AND sunday) = 7
    AND (SELECT count(*) FROM official_daily WHERE station_id = station AND source = 'kalshi'
         AND climate_date BETWEEN monday AND sunday) = 7

for each city (in ticker order):
    todo = [weeks where not done(...)]
    if not todo: continue
    db.backup.dump(f"before-backfill-{series}")            # one backup per city, before its first load
    for each week in todo:
        m_dir = api_probe/kalshi/{series}_{monday}_{sunday}
        w_dir = api_probe/weather/dump_{monday}_{sunday}/{station}
        steps = [
          ["uv","run","python","api_probe/kalshi/dump_kxhighny.py","--series",series,"--start",monday,"--end",sunday,"--no-trades","--out",m_dir],
          ["uv","run","python","-m","ingest.load_kxhighny_dump",m_dir],            # runs the QA gate first
          ["uv","run","python","api_probe/weather/dump_weather.py","--station",station,"--network",network,"--start",monday,"--end",sunday,"--out",w_dir],
          ["uv","run","python","-m","ingest.load_weather_dump","--station",station,"--dump-dir",w_dir,"--markets",m_dir/"markets.json"],
        ]
        run each step in order (subprocess, capture output, timeout 1800 s); stop at the first non-zero exit
        append one JSON line to ingest/backfill_log.jsonl:
          {"at": UTC now, "series", "station", "monday", "sunday",
           "status": "loaded" | "blocked" | "error", "failed_step": index or null,
           "tail": last 15 lines of the failed step's output (or of the last step on success)}
        print one line: series, week, status
    # a blocked or failed week never stops the run: the next week / city continues
print totals per status at the end
```
A partial week (Kalshi had no market on some day) is blocked by gate check 2 and logged; that is
expected and shown in the coverage report later, not worked around.

## Spec corrections (lead, after the build)
1. `blocked` vs `error`: a non-zero exit of a load step (index 1 or 3) whose output contains `BLOCK` is
   `blocked`; any other failure, including a timeout, is `error`.
2. `.gitignore` now ignores every series' dump folders: `api_probe/kalshi/KXHIGH*/` (lead).
3. **Partial weeks (found by QA):** when Kalshi has no market on some day, the dump script stops with
   "no markets found for event …", so the week is logged `error` at step 0 (nothing inserted), not
   `blocked` by gate check 2. Accepted: the log tail names the missing event, and the coverage report
   will show it. Example: KXHIGHTEWR 2026-08-24..30 (no markets on 24 and 25 Aug). Agreed test 5 is met
   with that real `error` week plus the fake-step checks of the `blocked` path.

## Agreed tests (`tests/ingest/check_backfill.py`, qa-tester)
1. `--dry` lists 24 cities × 13 weeks, marks KXHIGHNY 2026-11-16..22 and KXHIGHCHI 2026-11-16..22 as
   `skip` (already loaded) and everything else `todo`; it writes nothing (row counts and log file
   unchanged).
2. Week arithmetic: `--start` not a Monday or `--end` not a Sunday exits 1 with a message.
3. Real run on one city-week (`--series KXHIGHMIA --start 2026-11-16 --end 2026-11-22`): status
   `loaded`; KMIA has 7 kalshi and 7 nws_cli rows for those days, equal on every day; 7 KXHIGHMIA
   events; one log line; one new backup file named `*_before-backfill-KXHIGHMIA.dump`.
4. Rerun of the same command: the week is `skip`, nothing is fetched, no backup is taken, no rows change.
5. A blocked week is logged, not fatal: the qa-tester picks a real city-week that blocks (or reports
   that none exists in the window); the runner continues to the next week and exits 0.
6. Every existing suite still passes.

## Real run (lead, after review)
`uv run python -m ingest.backfill` in the background, ~5 GB of dumps, about 4–5 hours at the scripts'
pacing; progress via `ingest/backfill_log.jsonl` and one line per city in
`.claude/overnight/reports/track-a.md`. Track B is told after each city (B019 live runs).

## Test files
- `tests/ingest/check_backfill.py`

## Done when
- `uv run python -m tests.ingest.check_backfill` exits 0; every other suite still passes.

If anything here is unclear or looks wrong: stop and report, do not guess.
