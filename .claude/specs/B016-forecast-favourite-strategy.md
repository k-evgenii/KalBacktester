# B016 — Reference strategy: buy the forecast bracket

Track: B. Overnight queue item 3 (`.claude/overnight/PLAYBOOK.md` section 7; specs inside the
overnight queues count as user-approved).

## Goal
One simple, transparent strategy that turns market and weather data into order intents for the
matching engine: on each event day, at a fixed local morning time, average the models' forecast
daily high and buy YES on the bracket that contains it. It only produces intents; the engine
(B012) decides what fills. No P&L.

## Not built
No exits (positions are held to settlement; the engine's after-the-high lock stops later
orders anyway), no sizing logic, no price filter, no parameter search, no database writes.

## Files
- `strategies/__init__.py` (new, empty)
- `strategies/forecast_favourite.py` (new)

## Interfaces

```python
DEFAULTS = {"decision_time_lst": "09:00", "lead_day": 1, "min_models": 5, "quantity": 10}

def intents_for_event(conn, event_ticker: str, params: dict) -> tuple[list[dict], dict]
```
Returns `(intents, info)`: `intents` is a list of 0 or 1 intent dicts in the engine's format
(`decided_at`, `market_ticker`, `side`, `action`, `quantity`, `reason`); `info` is
`{"event_ticker", "decided_at", "n_models", "forecast_high", "chosen_market", "skipped": None | str}`.

```python
def intents_for_series(conn, series_ticker: str, start: date, end: date, params: dict)
        -> tuple[list[dict], list[dict]]
```
All events of the series with `start <= event_date <= end`, in date order; concatenated intents
and the list of `info` dicts.

Command line:
`uv run python -m strategies.forecast_favourite --series KXHIGHNY --start 2026-11-16 --end 2026-11-22 --out FILE.csv [--db NAME] [--quantity N]`
writes the intents as a CSV readable by `engine.match_intents` (same columns and formats as
`backtest/b014_intents.csv`) and prints one line per event: date, number of models, forecast
high, chosen market or the skip reason. SELECT only.

## Hard parts (use as written)

Decision time (local standard time, all year, like the NWS climate day):
```python
# station of the event: event -> underlying -> station (std_utc_offset_h, e.g. -5)
h, m = map(int, params["decision_time_lst"].split(":"))
decided_at = datetime.combine(event_date, time(h, m), tzinfo=timezone.utc) - timedelta(hours=offset)
# e.g. 09:00 LST on 2026-11-18 at offset -5 -> 2026-11-18 14:00 UTC
```

Climate day window in UTC: `day_start = datetime.combine(event_date, time(0), tzinfo=utc) - timedelta(hours=offset)`,
`day_end = day_start + 24 h`; hours with `day_start <= valid_time < day_end`.

Forecast high:
```sql
SELECT model, max(temp_f) AS model_high, count(temp_f) AS n_hours
FROM forecast_hourly
WHERE station_id = %(station)s AND lead_day = %(lead)s
  AND valid_time >= %(day_start)s AND valid_time < %(day_end)s
GROUP BY model
```
- Keep a model only if `n_hours = 24`.
- **No look-ahead:** a value at lead L for valid hour v was predicted at v − 24 h × L. Every kept
  hour must satisfy `valid_time - 24h*lead_day <= decided_at`; check this explicitly for the
  window (`day_end - 1 h - 24 h × lead <= decided_at`). If it fails, skip the event with reason
  `lookahead` (never use the data).
- Fewer than `min_models` kept → skip, reason `too_few_models`.
- `forecast_high` = mean of the kept `model_high` values (Decimal), rounded half up to a whole
  degree for the bracket choice (keep the unrounded mean in `info`).

Bracket: the market of the event in `market_bracket` with
`(temp_lo IS NULL OR temp_lo <= r) AND (temp_hi IS NULL OR r <= temp_hi)` where r is the rounded
forecast. None → skip, reason `no_bracket`; more than one → skip, reason `ambiguous_bracket`.

Intent: `{"decided_at": decided_at, "market_ticker": chosen, "side": "yes", "action": "buy",
"quantity": params["quantity"], "reason": f"forecast {mean:.1f}F from {n} models"}`.

Example (live data, 18 Nov 2026, lead 1): nine models, highs 58.0–62.7, mean ≈ 60.4 → r = 60 →
bracket 60–61 (`KXHIGHNY-26NOV18-B60.5`), decided 14:00 UTC. (The settled high was 64; the
strategy is a reference, not a good one.)

Clarification decided by the lead during the build: if the event's underlying has no station
(or the event is unknown), the event is skipped with reason `no_station` (no exception), so a
multi-city run never stops on one unlinked city.

## Agreed tests
`tests/backtest/check_forecast_favourite.py` (plain script, `[PASS]`/`[FAIL]`, exit 1 on any
failure; run with `uv run python -m tests.backtest.check_forecast_favourite`). Throwaway database
`kalbt_b016_test` with a hand-made fixture (station offset −5, one event, the six-bracket ladder,
forecasts for chosen models), dropped in a `finally`; expected values computed by hand.
1. Decision time: 09:00 LST at offset −5 → 14:00 UTC; at offset −8 → 17:00 UTC.
2. Five models with 24 hours each and known highs → mean and rounded value as computed by hand;
   the chosen market is the bracket containing it; one buy-YES intent with the given quantity.
3. A model with 23 hours is ignored; with only 4 complete models → skipped `too_few_models`.
4. Rounding: mean 61.5 → 62 (half up); mean 61.49 → 61; bracket choice follows.
5. Forecast rows outside the climate day (one hour before `day_start`, one at `day_end`) are not used.
6. `lead_day` 0 with a 09:00 decision → skipped `lookahead`.
7. Mean above the top bracket's floor → the `greater` market; below the `less` cap → the `less` market.
8. The CSV written by the CLI loads with `engine.match_intents.read_csv` into the same intents.
9. Live database, read-only: `intents_for_series(conn, "KXHIGHNY", 2026-11-16, 2026-11-22, DEFAULTS)`
   returns 7 info entries; every intent's market belongs to its event; on 2026-11-18 the chosen
   market is `KXHIGHNY-26NOV18-B60.5` at 14:00 UTC; the live trading tables are unchanged.

## Test files
- `tests/backtest/check_forecast_favourite.py` (new)

## Done when
The test script all PASS; the CLI writes 7 intents (or names each skipped day) for the November
week; nothing was written to any database.
