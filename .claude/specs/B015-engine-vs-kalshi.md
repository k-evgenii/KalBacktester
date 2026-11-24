# B015 — Engine fills checked against Kalshi's raw data

Track: B. Overnight queue item 2 (`.claude/overnight/PLAYBOOK.md` section 7; specs inside the
overnight queues count as user-approved).

## Goal
For one run, compare every fill the engine recorded with what Kalshi itself published: the raw
one-minute bar at the fill minute (from the dump files, not the database), the public trades
in that same minute, and the fee. Write a difference report. Reads only; writes nothing to the
database.

Why: reconciliation (B013) re-checks the engine against the **database**. This unit closes the
loop to the **source**: if the loader had translated a quote wrongly, B013 would not see it.

## Not built
No change to the engine, the loader or the database. No authenticated Kalshi calls (the real
per-trade fee needs an account). No new dependency.

## Files
- `backtest/compare_kalshi.py` (new)

## Interfaces

```python
def compare_run(conn, run_id: int, dump_dirs: list[Path]) -> dict
```
Command line: `uv run python -m backtest.compare_kalshi <run_id> [--dump DIR ...] [--db NAME]`.
Default dump dirs (absolute, main checkout; git-ignored data lives only there):
`/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY`,
`/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-06-01_2026-06-07`,
`/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY_2026-08-12_2026-08-18`.
A market's dump folder = the first dump dir that has `bars_1min/<ticker>.json`. Prints one
block per fill and a summary; writes the report as JSON to
`backtest/reports/compare_kalshi_run<run_id>.json`; exit 0 if every fill passes the quote
check (A), else 1. Parts B and C are informational and never change the exit code.

Report shape:
```python
{"run_id": int, "checked_at": str, "verdict": "PASS" | "FAIL",
 "fills": [{"outcome_id", "intent_id", "market_ticker", "side", "action", "occurred_at",
            "quantity", "price", "fee",
            "quote": {"status": "match" | "mismatch" | "no_bar" | "no_dump",
                      "bar_ts": str | None, "raw_bid": str | None, "raw_ask": str | None,
                      "kalshi_price": str | None},
            "trades": {"n": int, "volume": str, "min_price": str | None,
                       "max_price": str | None, "position": "inside" | "outside" | "no_trades"},
            "fee": {"engine": str, "formula_cents": str, "model_fee": str,
                    "difference_to_model": str, "sql_kalshi_fee": str | None}}],
 "summary": {"fills": int, "quote_match": int, "quote_other": int,
             "trades_inside": int, "trades_outside": int, "no_trades": int,
             "fee_formula_equal": int, "sql_kalshi_fee_available": bool}}
```

## Hard parts (use as written)

Fills come from the database (SELECT only):
```sql
SELECT o.outcome_id, o.intent_id, i.market_ticker, i.side, i.action, o.occurred_at,
       o.quantity, o.price, o.fee, (r.params ->> 'fee_rate')::numeric AS fee_rate
FROM order_outcome o
JOIN order_intent i ON i.intent_id = o.intent_id
JOIN run r          ON r.run_id = i.run_id
WHERE i.run_id = %s AND o.outcome = 'filled'
ORDER BY o.outcome_id;
```

**A. Quote check (decides the verdict).** From `bars_1min/<ticker>.json` (`candlesticks` list):
```python
def raw_close(bar, side):                    # side "yes_bid" | "yes_ask"
    q = bar.get(side) or {}
    v = q.get("close_dollars", q.get("close"))
    if v is None: return None
    v = Decimal(str(v))
    if side == "yes_bid" and v <= 0: return None   # 0.00 = no bid
    if side == "yes_ask" and v >= 1: return None   # 1.00 = no ask
    return v
# bar in force = the bar with the greatest end_period_ts <= occurred_at (epoch seconds)
# kalshi_price: buy yes -> ask; sell yes -> bid; buy no -> 1 - bid; sell no -> 1 - ask
# status: no dump folder -> "no_dump"; no bar at or before -> "no_bar";
#         needed side None -> "mismatch"; kalshi_price == price -> "match" else "mismatch"
```
Verdict PASS only if every fill's status is `match`.

**B. Trades in the fill minute (informational).** From `trades/<ticker>.json` (`trades` list):
trades with `occurred_at − 60 s < created_time <= occurred_at`. `volume` = sum of `count_fp`.
Price on our side: `yes_price_dollars` for YES orders, `no_price_dollars` for NO orders.
`position` = `inside` if `min_price <= our price <= max_price`, `outside` otherwise,
`no_trades` if there are none. A missing trades file counts as `no_trades`.

**C. Fee (informational).**
- `formula_cents` = `ceil(fee_rate × q × p × (1 − p) × 100) / 100` (what the engine uses).
- `model_fee` = `fee_rate × q × p × (1 − p)` rounded **up** to $0.000001 (Kalshi's docs: "model
  fee rounded up to $0.000001, plus a rounding fee, minus a rebate" — the rounding fee and
  rebate are not public, so this is the unrounded comparison).
- `difference_to_model` = `engine fee − model_fee`.
- `sql_kalshi_fee` = `SELECT kalshi_fee(q, p, fee_rate)` if that function exists in the
  database (Track C's C01), else None; check existence with
  `SELECT 1 FROM pg_proc WHERE proname = 'kalshi_fee'`.

All arithmetic in `Decimal`; prices printed with 4 decimals, fees with 6.

Clarifications decided by the lead during the build:
- The report has one `fee` key per fill (the dict); the engine's fee is `fee.engine`.
- If `kalshi_fee` exists but the call fails, `sql_kalshi_fee` is the string
  `"error: <message>"`, and the rest of the comparison continues (the failed statement is
  rolled back so the read-only transaction is not left aborted).
- `backtest/reports/` holds generated output and is not committed.

## Agreed tests
`tests/backtest/check_compare_kalshi.py`, plain script, `[PASS]`/`[FAIL]` per test, exit 1 on any
failure, run with `uv run python -m tests.backtest.check_compare_kalshi`.
1. Live run 31 (B014), read-only: verdict PASS; 5 fills, all quote status `match`; every fill
   has a `trades` entry and a `fee` entry; `formula_cents` equals the engine fee on all 5.
2. Throwaway fixture (database `kalbt_b015_test` + a temporary dump folder written by the test,
   one market): a fill whose raw dump bar has a different ask than the database candle →
   status `mismatch`, verdict FAIL, exit code 1 from the CLI.
3. Historical key style: the temporary dump bar uses `close` instead of `close_dollars` → read
   correctly (`match`).
4. Raw ask `1.0000` at the fill minute → treated as no ask → `mismatch` for a buy YES.
5. Trades window: a trade at exactly `occurred_at − 60 s` is excluded, one at `occurred_at`
   included; `position` computed from the included trades only.
6. Fee: 12 contracts at 0.47, rate 0.07 → `formula_cents` 0.21, `model_fee` 0.209244,
   `difference_to_model` 0.000756.
7. No dump folder for a market → status `no_dump`, verdict FAIL.
8. Read-only: live trading-table row counts unchanged; `compare_run` works on a connection
   with `read_only = True`.

## Test files
- `tests/backtest/check_compare_kalshi.py` (new)

## Done when
Run by the lead: the test script all PASS; `uv run python -m backtest.compare_kalshi 31` exits 0
and writes the report; the live database is unchanged.
