# B021 — Port the legacy benchmark `midday_fade` and let the harness run either strategy

Track: B backtest. Status: APPROVED (user, 2026-11-24, agreed in chat: port legacy midday_fade as the
second demo strategy and keep forecast_favourite; user away, lead proceeds with the agreed plan).

## Goal
`strategies/midday_fade.py` fades each day's favourite: at a fixed number of hours before the event's
close it buys NO on the bracket with the highest YES mid-price, if that mid is between `min_yes` and
`max_yes`. `backtest/run_all.py` gets `--strategy {forecast_favourite,midday_fade}` (default
`forecast_favourite`, so today's behaviour is unchanged).

## Not built
- No `armed_fade`, no sweep, no `min_threshold` (legacy param, always 0 there), no CSV output.
- No shared strategy base or registry module: the harness holds a two-entry dict.
- `intents_for_series` is a 10-line loop copied in shape from forecast_favourite; not factored out.

## Files
- `strategies/midday_fade.py` (new)
- `backtest/run_all.py` (only the changes below)

## Interfaces
```python
DEFAULTS = {"hours_before_close": 15, "min_yes": "0.50", "max_yes": "0.95", "quantity": 10}
def intents_for_event(conn, event_ticker, params) -> (list[dict], dict)
def intents_for_series(conn, series_ticker, start, end, params) -> (list[dict], list[dict])
```
Intent dicts exactly as forecast_favourite's (`decided_at, market_ticker, side, action, quantity,
reason`): side `"no"`, action `"buy"`, quantity `params["quantity"]`, reason
`f"fade favourite mid {mid:.3f} at {H}h before close"`.
Info dict keys: `event_ticker, decided_at, favourite_market, favourite_mid, skipped` where skipped is
None, `"no_quotes"` (no market of the event has a usable quote in the window) or `"no_favourite"` (quotes
exist but no mid is within [min_yes, max_yes]).
`intents_for_series`: every event of the series with start <= event_date <= end, by event_date (same as
forecast_favourite).
CLI: `uv run python -m strategies.midday_fade --series S --start D --end D [--db NAME]` prints one line
per event (`event_ticker decided_at favourite mid | skipped: reason`) and the intent count. SELECT only.

`backtest/run_all.py` changes:
- `import strategies.forecast_favourite as forecast_favourite`, `import strategies.midday_fade as midday_fade`,
  `STRATEGIES = {"forecast_favourite": forecast_favourite, "midday_fade": midday_fade}`.
- `run_series(conn, series, params, dry, save, strategy="forecast_favourite")`: calls
  `STRATEGIES[strategy].intents_for_series(...)` and `match_intents(conn, strategy, params, intents)`;
  the result dict gets `"strategy": strategy` as its first key.
- `main`: `--strategy` with `choices=sorted(STRATEGIES)`, default `"forecast_favourite"`; params
  `dict(STRATEGIES[args.strategy].DEFAULTS)`. Docstring line 1 becomes "Run a strategy over every loaded
  city series, reconcile each run, summarise." and the usage line shows `[--strategy NAME]`.
- Nothing else changes.

## Hard parts (use as written)
```python
QUOTES_SQL = """
    SELECT m.ticker, c.yes_bid, c.yes_ask
    FROM market m
    JOIN LATERAL (
        SELECT yes_bid, yes_ask FROM candle
        WHERE market_ticker = m.ticker AND ts <= %(t)s AND ts > %(t)s - interval '1 hour'
        ORDER BY ts DESC LIMIT 1
    ) c ON true
    WHERE m.event_ticker = %(e)s
    ORDER BY m.ticker
"""


def decision_time(close_time, hours_before_close):
    """Close rounded UP to the hour (the settlement hour), minus H hours (legacy _target_ts)."""
    anchor = close_time.replace(minute=0, second=0, microsecond=0)
    if close_time != anchor:
        anchor += timedelta(hours=1)
    return anchor - timedelta(hours=hours_before_close)


def intents_for_event(conn, event_ticker, params):
    info = {"event_ticker": event_ticker, "decided_at": None, "favourite_market": None,
            "favourite_mid": None, "skipped": None}
    # close_time: SELECT max(close_time) FROM market WHERE event_ticker = %s  (all markets share it)
    decided_at = decision_time(close_time.astimezone(UTC), params["hours_before_close"])
    info["decided_at"] = decided_at
    # rows = QUOTES_SQL with t = decided_at, e = event_ticker
    min_yes, max_yes = Decimal(params["min_yes"]), Decimal(params["max_yes"])
    quoted = [(ticker, (bid + ask) / 2) for ticker, bid, ask in rows if bid is not None and ask is not None]
    if not quoted:
        info["skipped"] = "no_quotes"
        return [], info
    best = None
    for ticker, mid in quoted:                 # ordered by ticker: ties keep the first ticker
        if min_yes <= mid <= max_yes and (best is None or mid > best[1]):
            best = (ticker, mid)
    if best is None:
        info["skipped"] = "no_favourite"
        return [], info
    info["favourite_market"], info["favourite_mid"] = best
    intent = {"decided_at": decided_at, "market_ticker": best[0], "side": "no", "action": "buy",
              "quantity": params["quantity"],
              "reason": f"fade favourite mid {best[1]:.3f} at {params['hours_before_close']}h before close"}
    return [intent], info
```
The engine fills the intent itself (quote one minute after `decided_at`, NO price = 1 − YES bid); the
strategy only reads candles with `ts <= decided_at`, so there is no look-ahead.

## Agreed tests (`tests/backtest/check_midday_fade.py`, qa-tester)
Throwaway database `kalbt_b021_test_<pid>` from `db/schema.sql`, dropped first and at the end. Fixture:
station KTST (offset −5), underlying U1, series S1; event `E1` on 2026-06-10 with six markets
`E1-A`..`E1-F`, open 2026-06-09 15:00 UTC, close 2026-06-11 05:00 UTC. Default params (H = 15 →
decision 2026-06-10 14:00 UTC). Candles (UTC, bid/ask):
`E1-A` 13:30 0.60/0.64; `E1-B` 13:59 0.70/0.74; `E1-C` 12:30 0.90/0.94 (older than 1 h);
`E1-D` 14:01 0.96/0.98 (after the decision); `E1-E` 13:50 NULL/0.10; `E1-F` none.
1. `intents_for_event(E1)` → one intent: decided_at 2026-06-10 14:00 UTC, `E1-B`, side no, action buy,
   quantity 10, reason "fade favourite mid 0.720 at 15h before close"; info favourite_mid 0.72,
   skipped None.
2. Event `E2` (2026-06-11), same ladder and candles one day later, close 2026-06-12 04:59 UTC →
   decided_at 2026-06-11 14:00 UTC (rounded up to 05:00 first), `E2-B` chosen.
3. E1 with min_yes "0.80" → no intent, skipped "no_favourite".
4. E1 with max_yes "0.70" → `E1-A` (mid 0.62) chosen.
5. E1 with hours_before_close 20 (decision 09:00 UTC; no candle in 08:00..09:00] → skipped "no_quotes".
6. Event `E3` (2026-06-12) where `E3-A` and `E3-B` both quote 0.70/0.74 at 13:59 the day before close →
   `E3-A` (tie keeps the first ticker).
7. `intents_for_series(S1, 2026-06-10, 2026-06-12, DEFAULTS)` → 3 infos in date order, 3 intents.
8. `decision_time` pure: close 05:00 H 15 → 14:00 previous day; close 04:59 → same; close 05:30 H 6 →
   00:00 same day.
9. Live, rolled back (no `--save`): `uv run python -m backtest.run_all --db kalbt --strategy midday_fade
   --series KXHIGHNY` exits 0 and prints `recon PASS` and `not saved`; live row counts of run,
   order_intent, order_outcome, settlement unchanged.
10. `uv run python -m backtest.run_all --db kalbt --dry` (default strategy) still exits 0, and the
    existing suites `tests/backtest/check_run_all.py` and `tests/backtest/check_forecast_favourite.py`
    still pass (run them with their documented commands; do not edit them).
11. CLI `uv run python -m strategies.midday_fade --series KXHIGHNY --start 2026-11-16 --end 2026-11-22`
    exits 0 and prints 7 event lines.

## Test files
- `tests/backtest/check_midday_fade.py`

## Legacy reference
`legacy/kbt/strategies/midday_fade.py` (MiddayFadeAt: whole file). Legacy used the bar mid at exactly
the target hour; here: the last candle with both quotes within the hour up to the decision.

## Done when
- `PYTHONPATH=. uv run python tests/backtest/check_midday_fade.py` exits 0 (covers 9–11)
- lead runs `uvx ruff check strategies/midday_fade.py backtest/run_all.py tests/backtest/check_midday_fade.py`

If anything here is unclear or looks wrong: stop and report, do not guess.
