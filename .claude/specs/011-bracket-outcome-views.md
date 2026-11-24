# 011 — Bracket and event-outcome views

Track: B (database). First of three units from the approved plan "matching engine and event
reconciliation" (011 views, 012 matching engine, 013 reconciliation).

## Goal
Two read-only views: `market_bracket` gives every contract one whole-degree temperature range,
a category and its position in the event's ladder; `event_outcome` shows, for every contract of
every event (traded or not), the settled temperature, Kalshi's result and the result our
bracket rule expects.

## Not built
No new tables, no stored data, no change to existing tables or triggers. No bracket logic for
non-temperature markets. No "distance from the winner" columns (the user chose shape and
ladder position only).

## Files
- `db/schema.sql` (edit: append the two views and their comments at the very end)
- `db/README.md` (edit: a short "Views" section placed immediately before `## Usage`)

## Interfaces

### `market_bracket` — one row per market
| column | type | meaning |
|---|---|---|
| `market_ticker` | text | the market |
| `event_ticker` | text | its event |
| `strike_type` | text | Kalshi's type, as stored |
| `temp_lo` | integer, NULL = no lower bound | lowest whole degree that makes the market YES |
| `temp_hi` | integer, NULL = no upper bound | highest whole degree that makes the market YES |
| `category` | text | `lower_tail` (less), `middle` (between), `upper_tail` (greater) |
| `ladder_pos` | integer | 1..n within the event, coldest bracket first |

Conversion (Kalshi's rule, verified on the loaded week: `less` is yes iff high < cap,
`greater` iff high > floor, `between` iff floor ≤ high ≤ cap):
- `less`, cap c → `temp_lo` NULL, `temp_hi` = c − 1
- `between`, floor f, cap c → `temp_lo` = f, `temp_hi` = c
- `greater`, floor f → `temp_lo` = f + 1, `temp_hi` NULL
- any other `strike_type` (or NULL) → both bounds NULL, `category` NULL

### `event_outcome` — one row per market, for every event
| column | type | meaning |
|---|---|---|
| `event_ticker`, `event_date` | text, date | the event |
| `market_ticker` | text | the market |
| `temp_lo`, `temp_hi`, `category`, `ladder_pos` | | from `market_bracket` |
| `settled_high_f` | numeric, NULL until known | the settled temperature: `official_daily.high_f` with `source = 'kalshi'` for the event's station and day |
| `result` | text, NULL until settled | Kalshi's result, from `market` |
| `expected_result` | text, NULL if `settled_high_f` or `category` is NULL | `yes` if the settled high is inside the bracket, else `no` |

## Hard parts (use as written)

```sql
CREATE VIEW market_bracket AS
SELECT
    m.ticker       AS market_ticker,
    m.event_ticker,
    m.strike_type,
    CASE m.strike_type
        WHEN 'between' THEN m.floor_strike::integer
        WHEN 'greater' THEN m.floor_strike::integer + 1
    END AS temp_lo,
    CASE m.strike_type
        WHEN 'between' THEN m.cap_strike::integer
        WHEN 'less'    THEN m.cap_strike::integer - 1
    END AS temp_hi,
    CASE m.strike_type
        WHEN 'less'    THEN 'lower_tail'
        WHEN 'between' THEN 'middle'
        WHEN 'greater' THEN 'upper_tail'
    END AS category,
    row_number() OVER (
        PARTITION BY m.event_ticker
        ORDER BY CASE m.strike_type
                     WHEN 'between' THEN m.floor_strike
                     WHEN 'greater' THEN m.floor_strike + 1
                 END NULLS FIRST,
                 m.ticker
    )::integer AS ladder_pos
FROM market m;

CREATE VIEW event_outcome AS
SELECT
    e.ticker      AS event_ticker,
    e.event_date,
    b.market_ticker,
    b.temp_lo,
    b.temp_hi,
    b.category,
    b.ladder_pos,
    o.high_f      AS settled_high_f,
    m.result,
    CASE
        WHEN o.high_f IS NULL OR b.category IS NULL THEN NULL
        WHEN (b.temp_lo IS NULL OR o.high_f >= b.temp_lo)
         AND (b.temp_hi IS NULL OR o.high_f <= b.temp_hi) THEN 'yes'
        ELSE 'no'
    END AS expected_result
FROM market_bracket b
JOIN market m     ON m.ticker = b.market_ticker
JOIN event e      ON e.ticker = b.event_ticker
JOIN underlying u ON u.underlying_id = e.underlying_id
LEFT JOIN official_daily o
       ON o.station_id   = u.station_id
      AND o.climate_date = e.event_date
      AND o.source       = 'kalshi';
```

Add `COMMENT ON VIEW` for both and `COMMENT ON COLUMN` for every view column, in the style of
the existing comments. The live database gets the same two `CREATE VIEW` statements and
comments once, applied by the lead (not by a worker).

## Agreed tests
(Approved by the user 2026-11-24.) New file, same conventions as `tests/db/test_schema.sql`:
everything inside `BEGIN; ... ROLLBACK;`, one DO block per case, `RAISE NOTICE 'PASS: ...'` /
`RAISE EXCEPTION 'FAIL: ...'`. Runs on a throwaway database built from `db/schema.sql`.

Fixture inserted by the test: station `KTST`, underlying `TST_HIGH` linked to it, one series,
event A on 2026-06-01 with the six markets of the real ladder shape (less cap 52; between
52–53, 54–55, 56–57, 58–59; greater floor 59), all unsettled, and event B on 2026-06-02 with
the same six strikes.

1. `market_bracket` bounds and category for event A are exactly:
   (NULL, 51, lower_tail), (52, 53, middle), (54, 55, middle), (56, 57, middle),
   (58, 59, middle), (60, NULL, upper_tail).
2. `ladder_pos` for event A is 1..6 in that order.
3. Before any official value exists, `event_outcome` has 12 rows and every `settled_high_f`
   and `expected_result` is NULL.
4. Insert `official_daily` (KTST, 2026-06-01, source `kalshi`, high 51): event A's
   `expected_result` is yes only for the less market.
5. Boundary cases, each with its own event day and official value (add events C, D, E with
   the same ladder, or reuse B): high 52 → yes only for 52–53; high 59 → yes only for 58–59
   (greater floor 59 is no); high 60 → yes only for the greater market.
6. For every event that has an official value, exactly one market has `expected_result = 'yes'`.
7. An `official_daily` row with source `nws_cli` alone (no `kalshi` row) leaves
   `settled_high_f` NULL.
8. A market with `strike_type` NULL has NULL bounds, NULL category and NULL `expected_result`.
9. The existing `tests/db/test_schema.sql` (63 cases) and `tests/db/test_weather_schema.sql`
   (46 cases) still pass unchanged (the table-list test counts base tables only).

Live-data check, run by the lead after applying the views (SELECT only):
`SELECT count(*) FILTER (WHERE expected_result IS NOT NULL), count(*) FILTER (WHERE result = expected_result) FROM event_outcome;`
→ the two numbers are equal (42 and 42 for the week 16–22 Nov 2026; events without a `kalshi`
official value have NULL `expected_result` and are not counted).

## Test files
- `tests/db/test_views.sql` (new)

## Legacy reference
`legacy/kbt/contracts.py` `outcome_locked` (the bracket rule; note its `less` comment is off by
one, which this unit corrects).

## Done when
Run by the lead:
1. Throwaway database (recipe in `db/README.md`, Usage): `db/schema.sql` applies cleanly;
   `tests/db/test_schema.sql` 63 PASS; `tests/db/test_weather_schema.sql` 44 PASS (Track A's unit
   009 relaxed two forecast checks; the "46" in agreed test 9 predates that);
   `tests/db/test_views.sql` all PASS, no FAIL or ERROR lines.
2. Views applied to the live database; the live-data check returns two equal numbers.

If anything here is unclear or looks wrong: stop and report, do not guess.
