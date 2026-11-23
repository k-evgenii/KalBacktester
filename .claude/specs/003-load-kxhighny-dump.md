# 003 — Load the one-week KXHIGHNY dump into the reconciliation database

Track: A ingestion. Status: APPROVED by the user 2026-11-23.

## Goal
Read the raw dump under `api_probe/kalshi/KXHIGHNY/` (unit 002) and insert it into the five
market-data tables of the reconciliation database: `underlying`, `series`, `event`, `market`,
`candle`. Running it twice must add nothing the second time.

## Not built
- Trades are not loaded (the database has no table for them).
- No fetching from the API, no other cities, no scheduling, no command-line options.
- No schema changes: `db/schema.sql` is off limits.

## Files
The implementer may create only:
- `db/connect.py` — one function `connect()`, shared by the loader, the tests and a notebook
- `ingest/load_kxhighny_dump.py` — the loader

Read for reference: `db/schema.sql`, `db/README.md`, `api_probe/kalshi/KXHIGHNY_FIELDS.md`,
and the dump files themselves.

## Interfaces

Run from the repo root: `uv run python -m ingest.load_kxhighny_dump`
(module form, so `from db.connect import connect` resolves; no `__init__.py` files).

Database: container `kalbt-recon-db`, already running with the schema applied, empty.
Money and prices are `numeric` dollars, volumes are `numeric`, timestamps `timestamptz` UTC.

The loader prints the number of rows inserted per table and exits 0.

## Hard parts (use as written)

`db/connect.py`:
```python
"""Connection to the reconciliation database (container kalbt-recon-db)."""
import os
from pathlib import Path

import psycopg


def read_password():
    """POSTGRES_PASSWORD from the environment, else from the repo's .env file."""
    if os.environ.get("POSTGRES_PASSWORD"):
        return os.environ["POSTGRES_PASSWORD"]
    env_file = Path(__file__).resolve().parent.parent / ".env"
    for line in env_file.read_text().splitlines():
        if line.startswith("POSTGRES_PASSWORD="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    raise RuntimeError("POSTGRES_PASSWORD not found in the environment or .env")


def connect():
    return psycopg.connect(
        host="127.0.0.1", port=5433, dbname="kalbt", user="kalbt", password=read_password()
    )
```
`.env` is only read, never written, printed or logged.

Mapping (all values passed as query parameters; use `Decimal(str)` for numbers so nothing goes
through a float):

| Table | Rows | Values |
|---|---|---|
| `underlying` | 1 | `underlying_id='NYC_HIGH'`, `description='Daily maximum temperature, New York Central Park (KNYC)'` |
| `series` | 1 | `ticker='KXHIGHNY'`, `underlying_id='NYC_HIGH'`, `title='Highest temperature in NYC'` |
| `event` | one per distinct `event_ticker` in `markets.json` | `ticker`, `series_ticker='KXHIGHNY'`, `underlying_id='NYC_HIGH'`, `event_date` = date parsed from the ticker part after the dash with `%y%b%d` (`26NOV22` → 2026-11-22), `settlement_source='The Weather Company'` |
| `market` | one per market | `ticker`, `event_ticker`, `strike_type`, `floor_strike` and `cap_strike` (`market.get(...)`, absent → NULL), `open_time`, `close_time`, `result` (`""` → NULL), `settled_at` = `settlement_ts` (absent → NULL) |
| `candle` | one per bar | `market_ticker`, `ts` = `end_period_ts` (epoch seconds → UTC timestamp), `yes_bid`, `yes_ask`, `volume` as below |

Bar values, for both key styles (live `_dollars` / `_fp`, historical plain):
```python
def bar_quote(bar, side):               # side is "yes_bid" or "yes_ask"
    quote = bar.get(side) or {}
    value = quote.get("close_dollars", quote.get("close"))
    return Decimal(value) if value is not None else None

bid = bar_quote(bar, "yes_bid")
ask = bar_quote(bar, "yes_ask")
if bid is not None and bid <= 0:  bid = None     # a bid of 0.00 means no bid
if ask is not None and ask >= 1:  ask = None     # an ask of 1.00 means no ask
volume = Decimal(bar.get("volume_fp", bar.get("volume")) or "0")
```

Insert order: underlying, series, event, market, candle, all in one transaction (commit once
at the end). Every insert ends with `ON CONFLICT DO NOTHING`. Count inserted rows with
`cursor.rowcount`. For candles use `cursor.executemany` per market.

## Agreed tests
Run against the real database after the loader has run. Expected numbers come from the dump
files, computed by the test itself.

1. Row counts: 1 underlying, 1 series, 7 events, 42 markets, and candles equal to the total
   number of bars in `bars_1min/` (41,158).
2. Per market: the number of candles and `sum(volume)` equal the bars file exactly (decimal
   comparison, no tolerance).
3. Quotes: per market, the number of candles with NULL `yes_bid` equals the bars whose closing
   bid is 0, and NULL `yes_ask` equals the bars whose closing ask is ≥ 1 (8,107 and 620 in
   total); for every other candle, `yes_bid` and `yes_ask` equal the bar's closing bid and ask.
4. Every candle `ts` lies within its market's `open_time`..`close_time` (inclusive).
5. For every market: `strike_type`, `floor_strike`, `cap_strike`, `open_time`, `close_time`
   and `result` equal `markets.json`, and `settled_at` equals `settlement_ts`.
6. Running the loader a second time exits 0, reports 0 rows inserted for every table, and
   leaves all row counts unchanged.

## Test files
- `tests/ingest/check_load_kxhighny_dump.py` — plain script, prints `[PASS]` / `[FAIL]` per
  check, exits 0 only if all pass; run with `uv run python -m tests.ingest.check_load_kxhighny_dump`
  from the repo root. It may import `db.connect` but must not import the loader's mapping
  functions: it recomputes expectations from the dump files itself.

## Legacy reference
none

## Done when
- `uv run python -m ingest.load_kxhighny_dump` exits 0
- `uv run python -m tests.ingest.check_load_kxhighny_dump` exits 0 with every check `[PASS]`

If anything here is unclear or looks wrong: stop and report, do not guess.
