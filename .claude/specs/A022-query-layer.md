# A022 — Query layer for the portfolio and the UI (DRAFT: field contract from Track C)

Track: A. Status: DRAFT — the full spec is written when A022 starts. This file records the field
contract Track C sent on 2026-11-24 so it is not lost. Every function returns a list of dicts;
times are UTC `timestamptz`; money and prices are `Decimal`. Read-only.

## Screen 3 — city / day market
- `cities()` → underlying_id, description, station_id, series_ticker
- `event_days(underlying_id)` → event_date, event_ticker, n_markets, settled (bool)
- `ladder(event_ticker)` → market_ticker, ladder_pos, temp_lo, temp_hi, category, result, settled_high_f
  (from Track B's `market_bracket` / `event_outcome` views)
- `quotes(event_ticker, step_minutes=15)` → market_ticker, ts, yes_bid, yes_ask, volume — one row per
  market per step: bid and ask of the last candle at or before `ts` (whole bar carried forward, never
  bid and ask filled separately); volume = sum over the step

## Screen 4 — weather against market (one event: its station, its climate day in local standard time)
- `observations(event_ticker)` → observed_at, temp_f
- `forecasts(event_ticker, lead_day=None)` → model, valid_time, lead_day, temp_f (None = all leads)
- `official(event_ticker)` → source, high_f, high_time_lst, low_f, issued_at

## Screen 5 — coverage (A023)
- `coverage()` → underlying_id, week_start (Monday), events, markets, bars, nws_highs, kalshi_highs,
  gate_warnings, numeric_mismatches, status ('OK' | 'Check' | 'RED')
  (gate warnings and numeric mismatches come from the backfill runner's records, A021)

When done: send Track C an INFO with the final module and function names.
