# 002 — Dump one week of KXHIGHNY (markets, 1-minute bars, trades)

Track: A ingestion. Status: APPROVED by the user 2026-11-23.

## Goal
Fetch every market of the New York daily-high series for a given range of settled event days
from the public Kalshi API, with its 1-minute bars and all public trades, and save the raw
responses as JSON. Write a data-quality report and a field description the database schema
can be designed from.

## Not built
- No database, no Polars, no conversion to cents: this is the raw dump only.
- No order books, no other cities, no scheduled daily run (later units).
- No command-line options beyond `--start` and `--end`.

## Files
The implementer may create only these:
- `api_probe/kalshi/dump_kxhighny.py` — the fetch script
- `api_probe/kalshi/KXHIGHNY_FIELDS.md` — field description, written after a real run
- everything under `api_probe/kalshi/KXHIGHNY/` — script output (ignored by git)

Read for reference: `api_probe/README.md`, `api_probe/kalshi/REPORT.md`,
`api_probe/kalshi/samples/`.

## Interfaces

Run: `uv run python api_probe/kalshi/dump_kxhighny.py --start 2026-11-16 --end 2026-11-22`
(both dates required, inclusive, event days). Standard library only (`urllib`, `json`,
`argparse`, `datetime`, `time`, `pathlib`). Plain functions, no classes.

Output, all under `api_probe/kalshi/KXHIGHNY/`:

| Path | Content |
|---|---|
| `markets.json` | `{"start", "end", "markets": [market objects exactly as returned]}`, sorted by `ticker` |
| `bars_1min/<ticker>.json` | `{"ticker", "surface": "live" or "historical", "candlesticks": [bars exactly as returned, oldest first]}` |
| `trades/<ticker>.json` | `{"ticker", "surface", "trades": [trades exactly as returned, newest first]}` |
| `_report.json` | the report below |

Files are written with `json.dumps(obj, indent=1, sort_keys=True)`. No timestamps of the run
itself are written anywhere, so a second run gives identical files.

## Hard parts (use as written)

```python
BASE = "https://api.elections.kalshi.com/trade-api/v2"
SERIES = "KXHIGHNY"
PAUSE = 0.5          # seconds after every request
MAX_BARS = 4999      # API refuses more than 5000 one-minute bars per call

def get(path, params=None):
    """GET BASE+path. Returns (status, parsed_json_or_None).
    Sends header User-Agent: kalbt-probe. Sleeps PAUSE after every request.
    On 429 or 5xx: sleep 5 s and retry, at most 3 retries, then raise RuntimeError.
    On 404: return (404, None). Any other 4xx: raise RuntimeError with the body."""

def event_ticker(day):            # date(2026, 11, 22) -> "KXHIGHNY-26NOV22"
    return f"{SERIES}-{day:%y}{day:%b}".upper() + f"{day:%d}"

def to_epoch(rfc3339):            # "2026-11-21T15:00:00Z" -> int seconds
    return int(datetime.fromisoformat(rfc3339.replace("Z", "+00:00")).timestamp())
```

Markets:
1. Page `GET /markets?series_ticker=KXHIGHNY&limit=1000&cursor=<c>` until the returned
   `cursor` is empty. Keep markets whose `event_ticker` is in the wanted set.
2. For every wanted event with no market from step 1, call
   `GET /historical/markets?event_ticker=<event>` and keep its markets.
3. An event that still has no markets: raise `RuntimeError` (the run fails).

Bars, per market:
```
start = to_epoch(open_time); end = to_epoch(close_time)
surface = "live"
for each chunk [lo, hi] covering start..end, hi = min(end, lo + MAX_BARS*60 - 1):
    live:        GET /series/KXHIGHNY/markets/<ticker>/candlesticks
    historical:  GET /historical/markets/<ticker>/candlesticks
    params: start_ts=lo, end_ts=hi, period_interval=1
    first chunk only: if the live call returns 404, set surface = "historical" and retry it
    append response["candlesticks"]
```
Bars are stored untouched. Live bars use `_dollars` / `_fp` key names, historical bars use
plain names; do not rename them.

Trades, per market, on the same surface as its bars:
`GET /markets/trades` (live) or `GET /historical/trades` (historical) with
`ticker=<ticker>&limit=1000&cursor=<c>`, paging until `cursor` is empty.

Report (`_report.json`) — descriptive flags only, none of them fails the run:
```
{"start", "end",
 "events": {<event_ticker>: {"n_markets", "empty": bool}},        # empty = no bars and no trades in any market
 "markets": {<ticker>: {
     "surface", "status", "result", "expiration_value",
     "open_minutes",            # (to_epoch(close_time) - to_epoch(open_time)) // 60
     "n_bars", "n_bars_no_trade",   # bars whose "price" has no close value
     "n_gaps",                  # missing minutes between first and last end_period_ts
     "n_trades",
     "bar_volume", "trade_volume",   # sum of bar volume (volume_fp or volume) and sum of trade count_fp, as floats
     "n_trades_after_close",    # trades with created_time later than close_time
     "event_day",               # from the ticker, "2026-11-22"
     "open_ny", "close_ny", "occurrence_ny",   # open_time, close_time, occurrence_datetime in America/New_York, ISO format
     "flags": [...]}},          # any of: "no_bars", "no_trades", "missing_result", "missing_expiration_value"
 "null_counts": {"markets": {field: n}, "bars": {field: n}, "trades": {field: n}}}
```
`null_counts` counts, per top-level field, how many records have it missing, `null`, `""` or
`{}`. Use `zoneinfo.ZoneInfo("America/New_York")` for the local times; they are reported
only, nothing is checked against them yet. A bar has no trade when `price` is `{}` or its close (`close_dollars` or `close`) is null.

After the script works, write `KXHIGHNY_FIELDS.md` from the real dump: one table each for
markets, bars and trades with field, type, unit, example, whether it was ever empty (from
`null_counts`), the live and historical name of each bar field side by side, and a suggested
Postgres column name and type (money as integer cents, counts as numeric). State only what
the dump shows.

## Agreed tests
Decided by the user and the lead. The qa-tester writes them as `api_probe/kalshi/check_dump.py`
from this list and the Interfaces section, without reading `dump_kxhighny.py` first. The script
reads the dump for 2026-11-16 to 2026-11-22, prints `[PASS]` / `[FAIL]` per check and exits 0
only if all pass.

1. `markets.json` holds exactly 7 distinct events and they equal the tickers generated from
   the date range (`KXHIGHNY-26NOV16` … `KXHIGHNY-26NOV22`); every market has
   `status == "finalized"`.
2. Every market has a bars file and a trades file, and its number of bars is at most
   `open_minutes + 1` (open minutes = whole minutes between `open_time` and `close_time`).
3. Settlement consistency. For every market whose `expiration_value` is not empty, with
   `v = float(expiration_value)`:
   - `strike_type == "greater"`: `result == "yes"` exactly when `v > floor_strike`;
   - `strike_type == "less"`: `result == "yes"` exactly when `v < cap_strike`;
   - `strike_type == "between"`: `result == "yes"` exactly when `floor_strike <= v <= cap_strike`.
   Markets with an empty `expiration_value` are printed as a note and do not fail the check.
4. Every bars file is sorted by `end_period_ts` ascending with no duplicate `end_period_ts`.
5. Every trades file is sorted by `created_time` descending, has no repeated `trade_id`, and
   every `created_time` parses and lies between the market's `open_time` and
   `latest_expiration_time` (inclusive).
6. Idempotence: the lead runs the dump twice and compares the folders (not in the script).

If a check fails because the data really is different, report it with the evidence; do not
change the check.

## Test files
- `api_probe/kalshi/check_dump.py`

## Legacy reference
`legacy/fetch_june.py` lines 101–286 (markets, candles, live-then-historical fallback). Do not
copy its candle renaming; this unit stores raw responses.

## Done when
- `uv run python api_probe/kalshi/dump_kxhighny.py --start 2026-11-16 --end 2026-11-22` exits 0
- `uv run python api_probe/kalshi/check_dump.py` exits 0 with every check `[PASS]` (qa-tester)
- `api_probe/kalshi/KXHIGHNY_FIELDS.md` exists

If anything here is unclear or looks wrong: stop and report, do not guess.
