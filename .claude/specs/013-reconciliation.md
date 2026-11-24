# 013 — Reconciliation of one run

Track: B. Third unit of the approved plan (011 views, 012 matching engine, 013 reconciliation).
STATUS: approved by the user 2026-11-24 (checks and the 15 tests).

## Goal
A script that takes one run id and independently re-checks everything recorded for that run
(intents, outcomes, settlements, snapshots) against the market data and the rules of spec 012.
It reports PASS or FAIL per check with examples. It writes nothing.

## Not built
No P&L or cash checks. No repair of anything it finds. No check across several runs.

## Independence (the point of this unit)
- `recon/check_run.py` must not import anything from `engine/`.
- It works a different way on purpose: plain SQL reads with psycopg into Python lists and
  dicts, `bisect` for "last candle at or before", `Decimal` arithmetic. No Polars. A mistake
  in the engine's Polars lookups therefore cannot be repeated here.
- Database access is SELECT only.

## Files
- `recon/__init__.py` (new, empty)
- `recon/check_run.py` (new)

## Interfaces

```python
def check_run(conn, run_id: int) -> dict
```
Returns, in the same shape as the QA gate's report:
```python
{"verdict": "PASS" | "FAIL", "run_id": int, "checked_at": str,
 "checks": [{"id": int, "name": str, "status": "pass" | "fail",
             "detail": str, "examples": [str, ...]}],      # at most 10 examples per check
 "counts": {"intents": int, "filled": int, "rejected": int, "blocked": int,
            "settlements": int, "snapshots": int}}
```
Verdict is FAIL if any check fails. An unknown `run_id` raises `ValueError`.

Command line: `uv run python -m recon.check_run <run_id> [--db NAME]` prints one line per check
(`[PASS]` / `[FAIL]` + id + name + detail, examples indented) and exits 0 on PASS, 1 on FAIL.

Parameters are read from `run.params`: `fill_delay_minutes`, `volume_window_minutes`,
`volume_share`, `fee_rate`.

## Definitions used by the checks (hard parts; write these as small functions)

- `fill_time(intent) = decided_at + fill_delay_minutes`.
- `quote_at(market, t)` = the candle of that market with the greatest `ts <= t`, or none.
  (Per market: candles sorted by ts; `bisect_right` on the ts list.)
- Needed side and price: buy yes → `yes_ask`, price = ask; sell yes → `yes_bid`, price = bid;
  buy no → `yes_bid`, price = 1 − bid; sell no → `yes_ask`, price = 1 − ask.
- `available(intent) = floor(volume_share × sum(candle.volume where
  decided_at − window < ts <= decided_at))` for that market.
- `lock(event)`: the `official_daily` row with source `nws_cli` for the station of the event's
  underlying and `climate_date = event_date`; `high_time_lst` like `'225 PM'`: split on the
  space, left-pad the digits to 4, parse as 12-hour time; local = event_date + that time;
  UTC = local − `station.std_utc_offset_h` hours; lock = that time with minutes and seconds
  set to 0. No row, or text that does not parse → no lock.
- `fee(q, p) = (fee_rate × q × p × (1 − p))` rounded **up** to the cent.
- Replay: walk the run's intents in the order of `intent_id`; keep `net[market]` (YES units).
  A filled row changes it: buy yes +q, sell yes −q, buy no −q, sell no +q.
  `held` for an intent = `net` if side is yes else `−net`, taken **before** that intent.
- Position at the end of UTC day D: replay of fills with `occurred_at` before D+1 00:00, set
  to 0 once the market's `settled_at` is before D+1 00:00. `avg_cost`: quantity-weighted
  average price of the buys that built the current position, unchanged by sells, reset when
  the position returns to 0.
- Snapshot days: from the UTC date of the first fill to the last day a position is open: a
  settled market is open through the day before its `settled_at` date; a held market that is
  not settled is open through the UTC date of its `close_time`.

## Checks

| id | Check | Catches |
|---|---|---|
| 1 | Every intent has 1 or 2 outcome rows; at most one is `filled`; if there are two, one is `filled` and the other `rejected` | lost or duplicated outcomes |
| 2 | Every outcome's `occurred_at` equals `fill_time(intent)` | wrong timing |
| 3 | A filled quantity is > 0 and ≤ the intent's quantity | overfills |
| 4 | A fill's price equals the price from `quote_at(market, occurred_at)` for the needed side, and that side is present | wrong price, mid-price, stale quote |
| 5 | A fill's `occurred_at` satisfies `open_time < t <= close_time` | trading outside market hours |
| 6 | A fill's event has a lock and `occurred_at < lock` | trading at or after the day's high |
| 7 | A fill's quantity ≤ `available(intent)` | fills beyond the volume cap |
| 8 | A fill's fee equals `fee(quantity, price)` | wrong fee |
| 9 | Replay: no sell fills more than `held`; no buy fills on the opposite side of a held position | naked shorts, impossible positions |
| 10 | Every non-fill is justified by the data (table below) | orders wrongly refused |
| 11 | `position_snapshot` rows for the run equal the positions computed from the replay: same set of (date, market), same `net_units`, same `avg_cost` | bookkeeping drift |
| 12 | `settlement` rows equal what the replay requires: one row for every market with a non-zero final position and a non-NULL `market.result`, with that `result`, `settled_at`, `net_units` and payout (units on the winning side); no other rows | missing, extra or wrong settlements |
| 13 | For every market named in the run, `event_outcome.result = expected_result` wherever `expected_result` is not NULL | bracket or data errors |

Check 10, what justifies each reason (a non-fill row is wrong if its condition is false):

| reason_code | Must be true |
|---|---|
| `market_not_open` | fill time is not inside `open_time < t <= close_time` |
| `high_time_unknown` | the event has no lock |
| `after_daily_high` | the event has a lock and fill time ≥ lock |
| `opposite_position` | a buy, and `held` of the other side is > 0 before the intent |
| `nothing_to_sell` | a sell, and either `held <= 0` (single row) or the fill equals `held` and is less than the quantity (second row) |
| `no_quote` | no candle at or before the fill time, or the needed side is NULL |
| `volume_cap` | `available < quantity` (and for a second row, the fill equals `available`) |
| anything else | always a failure: unknown reason |

Each example names the intent id, the market and the two values that disagree.

Clarifications decided by the lead during the build (2026-11-24):
- Check 11 compares `avg_cost` within 0.000001: the engine stores an unrounded decimal
  quotient, so an exact comparison could fail on repeating decimals.
- Check 13 skips a market whose `result` is NULL (not settled yet): that is an unresolved
  trade, not a contradiction. It fails only when both values are present and differ.
- Check 10: an intent that has a filled row is treated as having a "second row" for the
  `volume_cap` and `nothing_to_sell` conditions.

## Agreed tests
(To be confirmed by the user.) Plain script, `[PASS]`/`[FAIL]` per test, exit 1 on failure,
run with `uv run python -m tests.recon.check_check_run`. Never touches the live database:
it creates a throwaway database `kalbt_recon_test` (autocommit `CREATE DATABASE`), applies
`db/schema.sql`, inserts a small hand-made fixture, and drops the database in a `finally`.

The tests may use `engine.match_intents` to produce a clean run (the checker itself may not
import it). To plant an error, the test disables the append-only triggers **in the throwaway
database only** (`ALTER TABLE <table> DISABLE TRIGGER USER`), changes or deletes exactly one
thing, runs `check_run`, and expects FAIL with exactly the named check failing.

1. A clean engine run with fills, a partial fill, rejections, blocks, an early exit, a
   settlement and snapshots → verdict PASS, all 13 checks pass.
2. An intent with no outcome row (outcome deleted) → check 1.
3. An outcome's `occurred_at` moved by one minute → check 2.
4. A filled quantity raised above the intent's quantity → check 3.
5. A fill price changed to the mid-price → check 4.
6. A run containing a fill exactly at the lock time (inserted by hand) → check 6.
7. A filled quantity raised above the volume cap (but not above the intent) → check 7.
8. A fee lowered by one cent → check 8.
9. A sell fill inserted by hand for more than is held → check 9.
10. A `no_quote` rejection inserted by hand where a quote exists → check 10.
11. A snapshot's `net_units` changed; and a snapshot row deleted → check 11 (both).
12. A settlement row deleted; and a settlement payout changed → check 12 (both).
13. A market whose stored `result` contradicts its bracket and settled high (fixture row) →
    check 13.
14. An unknown `run_id` raises `ValueError`.
15. The live database's trading tables have the same row counts before and after, and
    `check_run` issues no statement other than SELECT (verified by running it on a connection
    opened read-only: `conn.read_only = True`).

## Test files
- `tests/recon/__init__.py` (new, empty), `tests/recon/check_check_run.py` (new)

## Legacy reference
None (the legacy system had no reconciliation).

## Done when
Run by the lead:
1. `uv run python -m tests.recon.check_check_run` → every test PASS, exit 0.
2. `grep -n "engine" recon/check_run.py` shows no import of the engine.
3. The live database's trading tables are still empty; engine tests (26) still pass.

If anything here is unclear or looks wrong: stop and report, do not guess.
