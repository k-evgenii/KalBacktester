# 004 — Ingestion QA gate for the KXHIGHNY dump

Track: B (database / data quality)
Source: `.claude/handoffs/track-b-ingestion-qa.md` (sections 4–7), with the changes the user
agreed on 2026-11-23 (listed under "Agreed changes to the handoff").

## Goal
A QA gate that reads a raw KXHIGHNY dump folder, runs the checks below, writes a report, and
returns PASS (the loader may insert) or BLOCK (nothing is inserted). The loader runs the gate
first and refuses to insert on BLOCK.

## Not built
No new table (no `load_batch`; `_qa_report.json` next to the dump is the record). No weather
checks. No fetching. No changes to the database or to `db/schema.sql`. No retry or repair of
bad data: the gate only reports.

## Agreed changes to the handoff
1. New rule inside check 9: **every market must be settled** (BLOCK). We only load finished
   weeks, so a loaded market never changes and check 16 can stay strict.
2. Check 11: bid >= ask is **WARN** (spec 001 deliberately allows crossed quotes); the other
   parts of check 11 stay BLOCK.
3. Check 3: a missing **trades** file is **WARN**; a missing bars file stays BLOCK.
4. Checks 8 and 17 are dropped (the database enforces 8; 17 cannot fail).
5. Checks 5 and 6 BLOCK a bar whose bid, ask **or volume** is missing under both key styles.
6. No `load_batch` table.

## Files
- `ingest/qa_kxhighny_dump.py` (new)
- `ingest/load_kxhighny_dump.py` (edit, two small changes only; see "Loader changes")

## Interfaces

### `ingest/qa_kxhighny_dump.py`
```python
def run_checks(dump_dir: Path, conn) -> dict
```
Returns
```python
{
  "verdict": "PASS" | "BLOCK",          # BLOCK if any BLOCK check failed
  "dump_dir": str,
  "checked_at": str,                    # UTC ISO time
  "checks": [                           # one entry per check, in id order
    {"id": int, "name": str, "severity": "BLOCK" | "WARN",
     "status": "pass" | "fail" | "skipped",
     "detail": str,                     # one line
     "examples": [str, ...]}            # at most 10, e.g. "KXHIGHNY-26NOV22-B67.5 @ 2026-11-22T14:05Z"
  ],
  "counts": {"markets": int, "events": int, "bars": int, "trades": int,
             "bid_0_to_null": int, "ask_1_to_null": int}
}
```
`main()` (run as `uv run python -m ingest.qa_kxhighny_dump [dump_dir]`, default dump dir = the
loader's `DUMP_DIR`): opens `connect()` from `db/connect.py`, calls `run_checks`, prints one line
per check (`[PASS]`, `[FAIL]`, `[WARN]`, `[SKIP]` + id + name + detail), writes the report as
`_qa_report.json` in the dump folder (indent 2), and returns exit code 0 on PASS, 1 on BLOCK.

Order and skipping: run checks in id order. If any of checks 1–6 fails with BLOCK, the dump
cannot be parsed reliably: every later check is reported `skipped` and the verdict is BLOCK.

Database access is **SELECT only**. The gate never reads `_report.json`.

### Parsing helpers (inside the module, used by the checks)
- Times: `datetime.fromisoformat(text.replace("Z", "+00:00"))`.
- Money and counts: `Decimal(str)`, never float.
- Bar quote: `bar[side]` is a dict; value = `close_dollars` if that key is present, else `close`
  if present, else **missing** (a failure for check 6, never NULL).
- Bar volume: `volume_fp` if present, else `volume` if present, else **missing** (check 6).
- Translation (same as the loader): bid `<= 0` → None ("no bid"); ask `>= 1` → None ("no ask").
  Count both in `counts`.
- Event date from ticker: `datetime.strptime(event_ticker.split("-")[1], "%y%b%d").date()`.
- Expected event ticker for a date: `"KXHIGHNY-" + day.strftime("%y%b%d").upper()`.

## Checks

| id | severity | check |
|---|---|---|
| 1 | BLOCK | `markets.json` exists and parses; `bars_1min/` and `trades/` are folders; every file in them parses |
| 2 | BLOCK | `start <= end`; the set of `event_ticker` in `markets.json` equals the expected tickers for every day `start..end` (report missing and extra) |
| 3 | BLOCK / WARN | every market has `bars_1min/<ticker>.json` (missing = BLOCK) and `trades/<ticker>.json` (missing = WARN); no extra files; each file's `ticker` equals its filename stem; `surface` in {live, historical} and the same in a market's bars and trades files (BLOCK) |
| 4 | BLOCK | every market has `ticker`, `event_ticker`, `strike_type`, `open_time`, `close_time`, `status`; times parse; strike fields required by the strike type parse as Decimal |
| 5 | BLOCK | every bar: `end_period_ts` is an int; bid, ask, volume parse as Decimal; raw bid and ask within [0, 1]; volume >= 0 |
| 6 | BLOCK | every bar has `yes_bid`, `yes_ask` and a volume under one of the two key styles (see parsing helpers) |
| 7 | BLOCK | strike shape: `less` has `cap_strike` only; `greater` has `floor_strike` only; `between` has both and floor < cap |
| 9 | BLOCK | every market is settled: `status == "finalized"`, `result` in {yes, no}, `settlement_ts` present and parses |
| 10 | BLOCK | settlement rule holds wherever `expiration_value` is present (see hard parts) |
| 11 | BLOCK / WARN | per market: `end_period_ts` strictly increasing (no duplicates), every bar time `t` satisfies `open_time < t <= close_time` (BLOCK); bars where translated bid and ask are both present and bid >= ask (WARN, list market and time) |
| 12 | BLOCK | per event: exactly one `less`, one `greater`; strikes tile with no gap or overlap (see hard parts) |
| 13 | BLOCK | per event: exactly one market has `result == "yes"` |
| 14 | WARN | market count per event != 6; `open_time != event day 00:00 UTC − 9 h` or `close_time != event day 00:00 UTC + 30 h` |
| 15 | WARN | minutes where every market of an event has a bar with both translated bid and ask present: sum of asks < 1 or sum of bids > 1 |
| 16 | BLOCK | same key, different value, against the database (see hard parts) |
| 18 | WARN | a market with no bars, no trades, or bars on fewer than 5 % of its open minutes (`n_bars / ((close_time − open_time) in minutes)`) |
| 19 | WARN | per market, `|sum(bar volume) − sum(trade count_fp)| > 0.5` |
| 20 | WARN | a trade with `created_time > close_time`; a settled market with empty or missing `expiration_value` |
| 21 | WARN (info) | always passes; detail shows `bid_0_to_null` and `ask_1_to_null` counts |

(Ids follow the handoff; 8 and 17 are dropped.)

## Hard parts (use as written)

Settlement rule (check 10), per market with a non-empty `expiration_value`:
```python
value = Decimal(m["expiration_value"])
floor = Decimal(str(m["floor_strike"])) if "floor_strike" in m else None
cap   = Decimal(str(m["cap_strike"]))   if "cap_strike"   in m else None
if   m["strike_type"] == "greater": expected = "yes" if value > floor else "no"
elif m["strike_type"] == "less":    expected = "yes" if value < cap else "no"
else:                               expected = "yes" if floor <= value <= cap else "no"
fail if m["result"] != expected
```

Strike tiling (check 12), per event:
```python
less     = [m for m in ms if m["strike_type"] == "less"]
greater  = [m for m in ms if m["strike_type"] == "greater"]
between  = sorted((m for m in ms if m["strike_type"] == "between"), key=floor)
fail unless len(less) == 1 and len(greater) == 1 and len(between) >= 1
fail unless cap(less[0]) == floor(between[0])
for a, b in zip(between, between[1:]):
    fail unless cap(a) + 1 == floor(b)
fail unless floor(greater[0]) == cap(between[-1])
```

Same key, different value (check 16). Normalise the dump exactly as the loader does, then
compare with what is stored:
```python
# markets
cur.execute("""SELECT ticker, event_ticker, strike_type, floor_strike, cap_strike,
                      open_time, close_time, result, settled_at
               FROM market WHERE ticker = ANY(%s)""", (dump_tickers,))
# incoming row per market: (ticker, event_ticker, strike_type,
#   Decimal floor or None, Decimal cap or None, parsed open_time, parsed close_time,
#   result or None, parsed settlement_ts or None)
# fail for every stored row whose tuple differs from the incoming one; example names
# the ticker and the first differing column.

# candles
cur.execute("""SELECT market_ticker, ts, yes_bid, yes_ask, volume
               FROM candle WHERE market_ticker = ANY(%s)""", (dump_tickers,))
stored = {(row[0], row[1]): row[2:] for row in cur.fetchall()}
# incoming: candle_row(ticker, bar) from ingest/load_kxhighny_dump.py, key (ticker, ts)
# fail for every key present in both with different (bid, ask, volume);
# example: "<ticker> @ <ts ISO>: stored (b, a, v) vs dump (b, a, v)".
# Keys only in the dump are new rows (fine); keys only in the database are not an error.
```
Compare Decimals as Decimals (`Decimal("0.4200") == Decimal("0.42")` is true) and datetimes as
timezone-aware datetimes.

`candle_row` and `event_date` are imported from `ingest.load_kxhighny_dump`; do not copy them.

## Loader changes (`ingest/load_kxhighny_dump.py`)
1. `main(dump_dir=DUMP_DIR)`: use `dump_dir` everywhere `DUMP_DIR` is used today; the command
   line takes an optional first argument as the dump folder (`sys.argv[1]` if given).
2. First thing inside the `with connect() as conn` block, before any INSERT:
   ```python
   from ingest.qa_kxhighny_dump import run_checks   # import here, not at the top: the gate imports this module
   report = run_checks(dump_dir, conn)
   if report["verdict"] != "PASS":
       print("QA gate: BLOCK, nothing inserted. See _qa_report.json in the dump folder.")
       sys.exit(1)
   ```
   The gate writes the report itself only when run as a script; when called from the loader,
   the loader writes `_qa_report.json` too (one line: `(dump_dir / "_qa_report.json").write_text(json.dumps(report, indent=2, default=str))`) before checking the verdict.
Nothing else in the loader changes.

## Agreed tests
(Approved by the user 2026-11-23 as handoff section 7 plus tests 12–14.) Each test copies the
real dump `/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY/` (skipping `_report.json` and
`_qa_report.json`) into a fresh temporary folder, makes **one** change, runs `run_checks`
against the live database, and checks the verdict and the failing check id. The real dump is
never modified.

1. Untouched copy → PASS; WARN entries are present in the report.
2. Remove all markets of one event from `markets.json` → BLOCK, check 2.
3. Delete one `bars_1min/<ticker>.json` → BLOCK, check 3.
4. Set one bar's `yes_ask.close_dollars` to `"abc"` → BLOCK, check 5.
5. Rename `close_dollars` to `closing` in one bar's `yes_bid` → BLOCK, check 6.
6. Flip one losing market's `result` from `no` to `yes` → BLOCK, checks 10 and 13.
7. Swap two consecutive bars of one market → BLOCK, check 11.
8. Change one `between` market's `floor_strike` so the ladder has a gap → BLOCK, check 12.
9. Change one bar's `volume_fp` in a market already in the database → BLOCK, check 16; the
   example names that market and timestamp.
10. Row counts of all 10 tables are identical before and after running tests 1–9.
11. The loader run on the copy from test 3 exits non-zero and inserts nothing (row counts
    unchanged).
12. Set one market's `status` to `active` and `result` to `""` → BLOCK, check 9.
13. Set one bar's bid above its ask (both strictly between 0 and 1) → PASS, check 11 has a WARN
    naming that market and time.
14. Remove `volume_fp` from one bar (it has no `volume` key) → BLOCK, check 6.

## Test files
- `tests/ingest/check_qa_kxhighny_dump.py` (new). Plain script in the style of
  `tests/ingest/check_load_kxhighny_dump.py`: one `[PASS]`/`[FAIL]` line per test, exit 1 if any
  test fails. Run with `uv run python -m tests.ingest.check_qa_kxhighny_dump`.

## Legacy reference
None. Reference code: `api_probe/kalshi/check_dump.py` (check style),
`tests/ingest/check_load_kxhighny_dump.py` (test style).

## Done when
Run by the lead from the worktree root (the worktree's `.env` is a link to the main checkout's,
so `connect()` finds the password; nobody prints it):
1. `uv run python -m ingest.qa_kxhighny_dump` → exit 0, verdict PASS on the real dump.
2. `uv run python -m tests.ingest.check_qa_kxhighny_dump` → all 14 PASS.
3. `uv run python -m ingest.load_kxhighny_dump` → gate PASS, then 0 rows inserted in every table.
4. `uv run python -m tests.ingest.check_load_kxhighny_dump` → Track A's 17 checks still PASS.

If anything here is unclear or looks wrong: stop and report, do not guess.
