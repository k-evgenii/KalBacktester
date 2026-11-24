# A022 — Read-only query layer for the UI (market and weather screens)

Track: A. Status: APPROVED (overnight plan PLAYBOOK §7; field contract from Track C, 2026-11-24).
Screen 5 (coverage) is A023.

## Goal
Small read-only functions that return plain rows (list of dicts) for Track C's screens 3 (city/day
market) and 4 (weather against market), with exactly the fields Track C asked for. They read only
the database; times come back as UTC `timestamptz`, prices and money as `Decimal`.

## Not built
- No caching, no classes, no ORM, no writes, no new dependency.
- No coverage (A023), no portfolio queries (Track C owns those views).

## Files
- `query/__init__.py` (empty), `query/market.py`, `query/weather.py`
- `tests/query/__init__.py`, `tests/query/check_queries.py` (qa-tester)

## Interfaces
Every function takes an open psycopg connection first and uses `cursor(row_factory=dict_row)`.
`query/market.py`:
- `cities(conn)` → underlying_id, description, station_id, series_ticker
- `event_days(conn, underlying_id)` → event_date, event_ticker, n_markets, settled
- `ladder(conn, event_ticker)` → market_ticker, ladder_pos, temp_lo, temp_hi, category, result, settled_high_f
- `quotes(conn, event_ticker, step_minutes=15)` → market_ticker, ts, yes_bid, yes_ask, volume
`query/weather.py`:
- `observations(conn, event_ticker)` → observed_at, temp_f
- `forecasts(conn, event_ticker, lead_day=None)` → model, valid_time, lead_day, temp_f
- `official(conn, event_ticker)` → source, high_f, high_time_lst, low_f, issued_at

## Hard parts (use as written)
```sql
-- cities
SELECT u.underlying_id, u.description, u.station_id, s.ticker AS series_ticker
FROM underlying u JOIN series s USING (underlying_id)
WHERE u.station_id IS NOT NULL ORDER BY s.ticker;

-- event_days
SELECT e.event_date, e.ticker AS event_ticker, count(m.ticker) AS n_markets,
       bool_and(m.result IS NOT NULL) AS settled
FROM event e JOIN market m ON m.event_ticker = e.ticker
WHERE e.underlying_id = %(u)s GROUP BY e.event_date, e.ticker ORDER BY e.event_date;

-- ladder
SELECT market_ticker, ladder_pos, temp_lo, temp_hi, category, result, settled_high_f
FROM event_outcome WHERE event_ticker = %(e)s ORDER BY ladder_pos;

-- quotes: one row per market per step; bid AND ask from the SAME candle, the last one at or before ts
-- (never filled separately); volume = sum over (ts - step, ts]; NULL bid/ask = no quote
WITH m AS (SELECT ticker FROM market WHERE event_ticker = %(e)s),
     span AS (SELECT min(c.ts) AS t0, max(c.ts) AS t1 FROM candle c JOIN m ON c.market_ticker = m.ticker),
     steps AS (SELECT generate_series(
                 date_bin(make_interval(mins => %(s)s), span.t0, TIMESTAMPTZ '2000-01-01 00:00+00')
                   + make_interval(mins => %(s)s),
                 span.t1 + make_interval(mins => %(s)s),
                 make_interval(mins => %(s)s)) AS ts FROM span)
SELECT m.ticker AS market_ticker, st.ts, last.yes_bid, last.yes_ask,
       (SELECT coalesce(sum(c.volume), 0) FROM candle c
         WHERE c.market_ticker = m.ticker AND c.ts > st.ts - make_interval(mins => %(s)s)
           AND c.ts <= st.ts) AS volume
FROM m CROSS JOIN steps st
LEFT JOIN LATERAL (SELECT c.yes_bid, c.yes_ask FROM candle c
                   WHERE c.market_ticker = m.ticker AND c.ts <= st.ts
                   ORDER BY c.ts DESC LIMIT 1) last ON true
ORDER BY m.ticker, st.ts;
```
Weather, for an event: its station via event → underlying → station, and its climate day (local
**standard** time) as a UTC window:
```sql
WITH ev AS (
  SELECT st.station_id, e.event_date,
         (e.event_date::timestamp - make_interval(hours => st.std_utc_offset_h)) AT TIME ZONE 'UTC' AS w0
  FROM event e JOIN underlying u ON u.underlying_id = e.underlying_id
  JOIN station st ON st.station_id = u.station_id WHERE e.ticker = %(e)s)
-- observations
SELECT o.observed_at, o.temp_f FROM observation_hourly o, ev
WHERE o.station_id = ev.station_id AND o.observed_at >= ev.w0 AND o.observed_at < ev.w0 + interval '24 hours'
ORDER BY o.observed_at;
-- forecasts (temp_f IS NOT NULL only)
SELECT f.model, f.valid_time, f.lead_day, f.temp_f FROM forecast_hourly f, ev
WHERE f.station_id = ev.station_id AND f.valid_time >= ev.w0 AND f.valid_time < ev.w0 + interval '24 hours'
  AND f.temp_f IS NOT NULL AND (%(lead)s::int IS NULL OR f.lead_day = %(lead)s::int)
ORDER BY f.model, f.lead_day, f.valid_time;
-- official
SELECT d.source, d.high_f, d.high_time_lst, d.low_f, d.issued_at FROM official_daily d, ev
WHERE d.station_id = ev.station_id AND d.climate_date = ev.event_date ORDER BY d.source;
```
(For New York, std offset −5: climate day 2026-11-22 = 05:00 UTC 22 Nov to 05:00 UTC 23 Nov.)

## Agreed tests (`tests/query/check_queries.py`, read-only on live)
A backfill is adding data while these run, so every test uses fixed events, never whole-table counts.
1. `cities` includes KXHIGHNY → NYC_HIGH / KNYC and KXHIGHCHI → MDW_HIGH / KMDW; every row has a station.
2. `event_days(NYC_HIGH)` contains 2026-11-16 … 2026-11-22 with 6 markets each and `settled` true.
3. `ladder('KXHIGHNY-26NOV22')`: 6 rows ordered 1..6; exactly one `result = 'yes'`, and its bracket holds
   `settled_high_f` (61).
4. `quotes('KXHIGHNY-26NOV22', 15)`: recompute from raw `candle` rows in Python — for every row, bid and
   ask equal those of the last candle at or before `ts` (same candle), volume equals the sum over
   (ts − 15 min, ts]; steps are 15 minutes apart, aligned to :00/:15/:30/:45; 6 markets.
5. `observations('KXHIGHNY-26NOV22')`: every `observed_at` is in [05:00 UTC 22 Nov, 05:00 UTC 23 Nov); at least
   24 rows; the maximum `temp_f` is within 2 °F of the official high.
6. `forecasts('KXHIGHNY-26NOV22')` returns several models and leads; `lead_day=1` returns only lead 1; all
   `valid_time` in the same window; no NULL `temp_f`.
7. `official('KXHIGHNY-26NOV22')`: two rows, `kalshi` and `nws_cli`, both high 61.
8. Chicago (`KXHIGHCHI-26NOV22`, std offset −6): the window is 06:00 UTC 22 Nov to 06:00 UTC 23 Nov.
9. The functions never write: the transaction status after each call is idle with no changes
   (call them inside a transaction and roll back; row counts of the trading tables unchanged).


## Spec correction (lead, after Track C's C08 QA)
Bug in the first version: the first step was `date_bin(t0) + step`, so a candle exactly on a bin edge at
`t0` fell in no window (repro: candles 12:00 vol 5, 12:10 vol 3, 12:40 vol 2, step 15 → summed volume 5,
should be 10). Both ends of the step series are the **ceiling** of t0 and t1 on the grid:
```sql
steps AS (SELECT generate_series(
            date_bin(make_interval(mins => %(s)s), span.t0 - interval '1 microsecond',
                     TIMESTAMPTZ '2000-01-01 00:00+00') + make_interval(mins => %(s)s),
            date_bin(make_interval(mins => %(s)s), span.t1 - interval '1 microsecond',
                     TIMESTAMPTZ '2000-01-01 00:00+00') + make_interval(mins => %(s)s),
            make_interval(mins => %(s)s)) AS ts FROM span)
```
Every candle then lies in exactly one window (ts − step, ts]. Added agreed tests:
10. For KXHIGHNY-26NOV22 and KXHIGHCHI-26NOV22, per market, the sum of `quotes().volume` equals the sum of
    `candle.volume`; the first step is the ceiling of the first candle's `ts`, the last the ceiling of the last.
11. Edge fixture in a throwaway database (schema plus a hand-made market): candles at 12:00 (5),
    12:10 (3) and 12:40 (2), step 15. Expect steps 12:00, 12:15, 12:30, 12:45 with volumes 5, 3, 0, 2.
Test 4's independent grid must use the same ceiling rule.

## Done when
- `uv run python -m tests.query.check_queries` exits 0.

If anything here is unclear or looks wrong: stop and report, do not guess.
