# A020 — Market dump, gate and loader work for any seeded series; Chicago proves it

Track: A. Status: APPROVED (overnight plan, PLAYBOOK §7, user 2026-11-24; split from A019). Includes the B018 loader hook-up.

## Goal
Remove the KXHIGHNY hard-wiring from the market dump script, the QA gate and the market loader, so
each works for any series seeded by migration 0002, reading the series' underlying and station from
the database. Prove it end to end on Chicago (KXHIGHCHI, station KMDW) for 16–22 Nov 2026, market
and weather.

## Not built
- No renaming of files or modules (`dump_kxhighny.py`, `load_kxhighny_dump.py`, `qa_kxhighny_dump.py`
  keep their names; docstrings say "any daily-high series").
- No backfill runner (A021). The weather dump and loader are already generic (`--station`,
  `--network`, `--dump-dir`, `--markets`; the loader finds each event's station via
  series → underlying) and are not changed.

## Files
- `api_probe/kalshi/dump_kxhighny.py` — `--series` (default `KXHIGHNY`), `--no-trades`
- `ingest/qa_kxhighny_dump.py` — series taken from the dump
- `ingest/load_kxhighny_dump.py` — series, underlying from the database
- `ingest/load_weather_dump.py` — call Track B's weather QA gate before inserting (below)
- `tests/ingest/check_load_kxhighny_dump.py`, `tests/ingest/check_qa_kxhighny_dump.py` — only the
  changes listed under "Test changes" (qa-tester)

## Hard parts (use as written)

Series of a dump, one function in `ingest/load_kxhighny_dump.py`, imported by the gate:
```python
def series_of(markets):
    """The one series ticker of a dump's markets (event ticker part before the first '-')."""
    found = {m["event_ticker"].split("-")[0] for m in markets}
    if len(found) != 1:
        raise ValueError(f"a dump must hold exactly one series, found {sorted(found)}")
    return found.pop()
```

Dump script:
- `--series S` (default `KXHIGHNY`) replaces the constant everywhere (event tickers, `/markets`
  paging, live candle path `/series/{S}/markets/{ticker}/candlesticks`).
- Default `--out` becomes `api_probe/kalshi/<S>/`.
- `--no-trades`: no trade requests and no `trades/` folder. `_report.json` records `"trades": false`
  and the trade fields of each market are null.

Gate:
- Check 1 fails (BLOCK) if `series_of` raises.
- Check 2 builds the expected event tickers from that series.
- `station_of(conn, series)` takes the series as a parameter; every message names the series.
- Checks 14 and 22 pass the dump's series.
- Missing `trades/` folder: check 3's missing-trades WARN becomes one line, "dump has no trades
  (fetched with --no-trades)", when `_report.json` says `"trades": false`. Checks 19 and 20 report
  "skipped: no trades" as a pass with that detail (no per-market warnings).

Loader:
- `series = series_of(markets)`.
- `SELECT underlying_id FROM series WHERE ticker = %s`; no row → exit 1 with "series <S> is not
  seeded: run python -m db.migrate".
- It no longer inserts `underlying` or `series` rows (they come from migrations).
- Events are inserted with that series and underlying; the source per event is as before.
- It prints rows inserted for `event`, `market` and `candle` only.


Weather loader: run Track B's gate (B018, `ingest/qa_weather_dump.py`) before any insert. The gate
imports from the loader, so import it **inside `main()`** to avoid a circular import:
```python
    from ingest.qa_weather_dump import run_checks      # inside main(), after station_check(...)
    with connect() as conn:
        report = run_checks(conn, station, dump_dir, args.markets)
    if report["verdict"] != "PASS":
        print("weather QA gate: BLOCK, nothing inserted")
        for check in report["checks"]:
            if check["status"] == "block":
                print(f"  {check['id']}: {check['detail']}")
        sys.exit(1)
```
The gate's own CLI and report file are unchanged; the loader does not write `_weather_qa_report.json`.


## Spec corrections (lead, 2026-11-24, after the Chicago run)
1. **Check 14, opening time.** Real data: every series opens at **10:00 America/New_York** the day
   before the event day (Chicago 09:00 local = 10:00 ET; New York 10:00 local), and closes at 00:59 or
   01:00 in the **station's** local time the day after. Change only the opening rule in check 14:
   `expected_open = datetime(day - 1 day, 10:00, tzinfo=ZoneInfo("America/New_York"))`; the closing
   rule stays in the station's time zone. New York results are unchanged (Eastern either way).
   Agreed test 2 then expects check 14 to **pass** on the Chicago dump.
2. **Accepted deviation.** The loader looks up the series ("not seeded") before running the gate, so
   an unseeded series gets the clear message; a two-series dump exits 1 with the `series_of` error.

3. **More test scoping (approved after QA round 1).** `tests/ingest/check_wording.py` test 5 is scoped to
   KXHIGHNY like test 4; `tests/ingest/check_load_weather_dump.py` test 5a joins only events whose
   station is KNYC (`AND st.station_id = 'KNYC'`). Both counted every city's events by date; expected
   values unchanged. `tests/ingest/check_load_weather_dump.py` is added to the qa-tester's files.

4. **Accepted (review):** check 18 (coverage) also skips its per-market "no trades" problem when the
   dump was made with `--no-trades`; check 3's one-line WARN follows `_report.json` `"trades": false`.

## Test changes (lead-approved; qa-tester applies them, weakening nothing)
- `check_load_kxhighny_dump.py`: (the underlying/series scoping was done in A019.) Test 6b expects exactly the three
  printed lines (`event`, `market`, `candle`), each 0.
- `check_qa_kxhighny_dump.py`: any test that asserts a message text containing "KXHIGHNY unknown"
  keeps asserting it (the series is KXHIGHNY in those dumps).

- `tests/ingest/check_wording.py` (test 4, from unit 015): reads every event in the database and expects
  exactly 21, all New York. Scope the query to `series_ticker = 'KXHIGHNY'`; 4a and 4b keep their
  meaning (21 NY events, exactly the NY dump events). Otherwise Chicago's 7 events turn it red.

## Real runs (implementer; live APIs and live database; approved)
1. `uv run python api_probe/kalshi/dump_kxhighny.py --series KXHIGHCHI --start 2026-11-16 --end 2026-11-22 --no-trades --out api_probe/kalshi/KXHIGHCHI_2026-11-16_2026-11-22`
2. Gate on it: PASS (report every WARN).
3. Load: `uv run python -m ingest.load_kxhighny_dump api_probe/kalshi/KXHIGHCHI_2026-11-16_2026-11-22`;
   rerun inserts 0.
4. Weather: `uv run python api_probe/weather/dump_weather.py --station KMDW --network IL_ASOS --start 2026-11-16 --end 2026-11-22 --out api_probe/weather/dump_2026-11-16_2026-11-22/KMDW`,
   then `uv run python -m ingest.load_weather_dump --station KMDW --dump-dir api_probe/weather/dump_2026-11-16_2026-11-22/KMDW --markets api_probe/kalshi/KXHIGHCHI_2026-11-16_2026-11-22/markets.json`
   (the station check must say `-> ok`); rerun inserts 0.
5. The three NY dumps still pass the gate; reloading the NY November dump inserts 0.
Stop and report on any BLOCK; never change checks or data to pass.

## Agreed tests (`tests/ingest/check_any_series.py`, qa-tester)
1. `series_of`: one series → it; two series → ValueError.
2. Gate on the Chicago dump: PASS; checks 22 and 23 pass with KMDW's code `(CLIMDW)`; check 14
   quiet or warning only.
3. A NY dump copy with one market's event ticker changed to KXHIGHCHI → check 1 BLOCK (two series).
4. Live database after the loads: 7 KXHIGHCHI events for 2026-11-16…22, all with underlying
   `MDW_HIGH`; each has 6 markets (or the dump's count), exactly one `yes`; every candle `ts` within its
   market's open..close.
5. KMDW: 7 `nws_cli` and 7 `kalshi` rows, equal on every day; for every Chicago event the kalshi high
   falls in the winning market's strike range.
6. The loader on a dump of an unseeded series (a copy with tickers renamed to `KXHIGHZZZ`) exits 1 with
   the "not seeded" message and inserts nothing (row counts unchanged).
7. Weather gate hook-up: a temporary copy of the November KNYC weather dump with one CLI `high`
   changed by 1 on a day already loaded (Kalshi value unchanged, so the numeric check only warns) →
   the loader exits 1 with "weather QA gate: BLOCK, nothing inserted" and no row count changes.
8. Every existing suite still passes (gate, wording, NY market and weather load checks, station
   numbers, engine, recon, portfolio, B014 dry run, migrate, schema tests on a throwaway database).

## Test files
- `tests/ingest/check_any_series.py` (new)
- `tests/ingest/check_load_kxhighny_dump.py`, `tests/ingest/check_qa_kxhighny_dump.py`, `tests/ingest/check_wording.py` (listed changes only)

## Done when
- Chicago market and weather data for 16–22 Nov are loaded through the gate and the station check
- every suite in agreed test 7 exits 0, and `check_any_series` exits 0

If anything here is unclear or looks wrong: stop and report, do not guess.
