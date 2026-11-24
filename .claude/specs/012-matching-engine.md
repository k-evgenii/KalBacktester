# 012 — Matching engine

Track: B. Second unit of the approved plan (011 views, 012 matching engine, 013 reconciliation).
STATUS: approved by the user 2026-11-24 (rules, interface and the 18 tests).

## Goal
A script that takes a list of order intents for one run and records in the database exactly
what happened to each: filled, rejected or blocked, with price, quantity, fee and reason; then
the settlements of positions still held and a daily snapshot of positions. It registers
events only.

## Not built
No strategy, no P&L, no cash limit, no equity curve, no fills spread over several minutes, no
order book. `run.starting_cash` is required by the schema and is stored as given but not used.

## Files
- `engine/__init__.py` (new, empty)
- `engine/match_intents.py` (new)
- `db/connect.py` (edit: `connect(dbname="kalbt")`, nothing else)
- `pyproject.toml`, `uv.lock` (add `polars`, via `uv add polars`)

## Interfaces

```python
def match_intents(conn, strategy: str, params: dict, intents: list[dict]) -> int
```
- `conn`: an open psycopg connection. Everything is written in one transaction; the function
  commits at the end, or rolls back and re-raises on any error (nothing half-written).
- `intents`: dicts with `decided_at` (timezone-aware datetime, UTC), `market_ticker` (str),
  `side` ("yes" | "no"), `action` ("buy" | "sell"), `quantity` (int > 0), `reason` (str or None).
- `params` (stored in `run.params` together with the defaults actually used):
  `fill_delay_minutes` (default 1), `volume_window_minutes` (60), `volume_share` ("0.10"),
  `fee_rate` ("0.07"), `starting_cash` ("1000").
- Returns the new `run_id`.
- An intent naming a market that is not in `market`, or with an invalid side/action/quantity,
  raises `ValueError` before anything is written.

Command line: `uv run python -m engine.match_intents <intents.csv> --strategy NAME [--db NAME]`
(CSV columns = the intent fields, `decided_at` in ISO format with offset). Prints the run id
and a count of outcomes by outcome and reason.

Rows written:
- `run`: strategy, params, `code_version` (output of `git rev-parse --short HEAD`, or
  "unknown"), starting_cash, `date_start` / `date_end` (UTC dates of the first and last
  `decided_at`), status `running`, then set to `done` with `finished_at` at the end.
- `order_intent`: one per intent, in processing order.
- `order_outcome`: one or two per intent (below). `occurred_at` = the fill time for every
  outcome. `price` = price of the side traded. `quantity` 0 for rejected/blocked.
- `settlement`, `position_snapshot`: see "After the last intent".

## The rules (hard parts; use as written)

Intents are processed in order of `decided_at`, ties in input order. State: `net[ticker]`,
an int, YES units (positive = holding YES, negative = holding NO), starting at 0.

For one intent, `fill_time = decided_at + fill_delay_minutes`. The first rule that applies
decides the outcome:

```
1. not (market.open_time < fill_time <= market.close_time)
       -> rejected  market_not_open
2. lock = lock_time(event of the market)            # see below
   lock is None                                     -> blocked  high_time_unknown
   fill_time >= lock                                -> blocked  after_daily_high
3. action == buy and side == yes and net < 0        -> blocked  opposite_position
   action == buy and side == no  and net > 0        -> blocked  opposite_position
4. held = net if side == yes else -net              # units of that side we hold
   action == sell and held <= 0                     -> rejected  nothing_to_sell
5. quote = last candle of the market with ts <= fill_time (none -> no quote)
   buy yes : needs yes_ask ; price = yes_ask
   sell yes: needs yes_bid ; price = yes_bid
   buy no  : needs yes_bid ; price = 1 - yes_bid
   sell no : needs yes_ask ; price = 1 - yes_ask
   no candle, or the needed side is NULL            -> rejected  no_quote
6. available = floor(volume_share * sum(candle.volume
                     where decided_at - window < ts <= decided_at))
   fill = min(quantity, available)  ; for a sell also min(.., held)
   fill == 0                                        -> rejected  volume_cap
7. filled row: quantity = fill, price, fee = fee(fill, price)
   if fill < quantity: a second row, rejected, reason volume_cap
   update net: buy yes +fill, sell yes -fill, buy no -fill, sell no +fill
```

Decided by the user (2026-11-24): rule 2 stops **sells as well as buys** after the day's high,
so a position still open at the lock is held to settlement (legacy locked buys only).

Lock time of an event (rule 2):
```python
# official_daily row: station of the event's underlying, climate_date = event_date, source 'nws_cli'
# high_time_lst is text like '225 PM' or '1105 AM' (local standard time, all year)
digits, ampm = high_time_lst.split()
t = datetime.strptime(digits.zfill(4) + ampm, "%I%M%p").time()
local = datetime.combine(event_date, t)                       # naive, local standard time
utc = (local - timedelta(hours=station.std_utc_offset_h)).replace(tzinfo=timezone.utc)
lock = utc.replace(minute=0, second=0, microsecond=0)         # top of that hour
# no row, NULL or unparsable high_time_lst -> lock is None
```
Example: high at "225 PM" on 2026-11-16, offset −5 → 19:25 UTC → lock 19:00 UTC.

Fee (exact decimals, rounded up to the cent):
```python
fee = (Decimal(fee_rate) * fill * price * (1 - price)).quantize(Decimal("0.01"), rounding=ROUND_CEILING)
```
This is the usual Kalshi quadratic fee; the API probe confirmed "quadratic, multiplier 1" but
not the formula text, so it is an assumption recorded in `run.params`.

Lookups with Polars (rules 5 and 6 do not depend on our position, so they are computed for all
intents at once before the loop):
- load `candle` rows for the markets named in the intents (`pl.read_database`), sorted by ts;
- quote: `intents.join_asof(candles, left_on="fill_time", right_on="ts", by="market_ticker", strategy="backward")`;
- volume: for each intent the sum of `volume` over its window (a join on market_ticker
  followed by a filter on the window and a group-by sum is fine at this size).
The loop then walks the prepared rows in order and applies rules 1–7 with `net`.
All prices and volumes stay `Decimal` (Polars `Decimal` dtype or converted on read), never float.

After the last intent:
- **Settlement:** for every market with `net != 0` and `market.result` not NULL: one row with
  `settled_at = market.settled_at`, `result`, `net_units = net`, and
  `payout = net if (net > 0 and result == 'yes') else -net if (net < 0 and result == 'no') else 0`.
  A held market that is not settled gets no row (it is an unresolved position).
- **Snapshots:** for each UTC date D from the date of the first fill to the last date on which
  any position is still open: one row per market whose position at the end of D is non-zero,
  where the position at the end of D = fills with `occurred_at` before D+1 00:00 UTC, set to 0
  once `settled_at` is before D+1 00:00 UTC. `avg_cost` = quantity-weighted average price of
  the fills that built the current position (reset when the position returns to 0); for a NO
  position it is the average NO price paid.

Clarifications decided by the lead during the build (2026-11-24):
- Snapshot range: from the UTC date of the first fill to the last date a position is open.
  A settled market's position is open through the day before its `settled_at` date; a held
  market that never settles is snapshotted through the UTC date of its `close_time`.
- `run.params` holds the five parameters only (caller's values merged over the defaults).
- An empty intents list raises `ValueError`; `quantity` must be a Python int > 0.
- Remainder reason (rule 7): when a sell is cut short because we hold less than
  min(quantity, available), the second row's reason is `nothing_to_sell`; when it is cut short
  by the volume cap (or for any buy) it is `volume_cap`.
- Extra tests added after the first review: snapshots of a held unsettled market; volume after
  `decided_at` is not counted; a filled sell NO; a sell larger than the holding; a fill exactly
  at `close_time`; lock times for '1200 PM', '1230 AM' and '1100 PM'.

## Agreed tests
(To be confirmed by the user.) The journal tables are append-only, so tests never write to the
live database. The test script creates a throwaway database `kalbt_engine_test` (via a psycopg
autocommit connection: `CREATE DATABASE`), applies `db/schema.sql`, inserts a small hand-made
fixture, runs the scenarios through `match_intents`, and drops the database at the end.

Fixture: one station (offset −5), underlying, series, one event on 2026-06-01 with three
markets (open 2026-05-31 15:00 UTC, close 2026-06-02 06:00 UTC); an `official_daily` `nws_cli`
row with high time "300 PM" (lock 20:00 UTC on 06-01); hand-made candles with known bids, asks
and volumes; market M1 settled yes, M2 settled no, M3 unsettled.

1. Buy YES fills at the ask of the quote in force at T + 1 min.
2. Buy NO fills at 1 − bid; the position goes negative.
3. Sell part of a YES holding at the bid; the position is reduced.
4. Sell with nothing held → rejected `nothing_to_sell`.
5. Needed side NULL in the quote → rejected `no_quote`; also no candle yet → `no_quote`.
6. Fill time before open, and after close → rejected `market_not_open`.
7. Request above the cap → one filled row (the cap) and one rejected `volume_cap` row.
8. No volume in the window → one rejected `volume_cap` row, nothing filled.
9. Fill time at or after the lock → blocked `after_daily_high`; an event with no `nws_cli`
   row → blocked `high_time_unknown`.
10. Buy the opposite side while holding → blocked `opposite_position`.
11. The quote changes between T and T + 1 min → the T + 1 quote is used.
12. No candle exactly at T + 1 min → the previous candle's quote is used.
13. Held to settlement on a winning and on a losing market → settlement rows with payouts
    equal to the units and 0; a position closed before settlement → no settlement row; a
    held unsettled market → no settlement row.
14. Snapshots: one row per open position per UTC day end, with the right `net_units` and
    `avg_cost`, none after settlement.
15. The fee on a fill equals the formula (one hand-computed value, e.g. 12 contracts at 0.47).
16. An intent with an unknown market raises and leaves no rows at all.
17. The `run` row ends with status `done`, `finished_at` set, and `params` holding the defaults.
18. The live database's `run`, `order_intent`, `order_outcome`, `settlement` and
    `position_snapshot` tables have the same row counts before and after the test script.

## Test files
- `tests/engine/__init__.py` (new, empty), `tests/engine/check_match_intents.py` (new; plain
  script, one `[PASS]`/`[FAIL]` line per test, exit 1 on any failure; run with
  `uv run python -m tests.engine.check_match_intents`)

## Legacy reference
`legacy/kbt/fills.py` (touch prices, fee), `legacy/kbt/engine.py` `run_event` (order loop),
`legacy/kbt/gate.py` `build_gate_from_max` (the lock). Behaviour differences are listed in the
approved plan, section 2.

## Done when
Run by the lead:
1. `uv run python -m tests.engine.check_match_intents` → every test PASS, exit 0.
2. Existing suites still pass (schema tests on a throwaway database; QA gate tests; Track A's
   load checks), since `db/connect.py` changed.
3. The live database's trading tables are still empty.

If anything here is unclear or looks wrong: stop and report, do not guess.
