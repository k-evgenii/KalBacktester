# KXHIGHNY dump: field description

Source: the dump of event days 2026-11-16 to 2026-11-22 (`api_probe/kalshi/KXHIGHNY/`): 7 events, 42 markets
(6 per event), 41,158 one-minute bars, 58,784 trades. All 42 markets came from the live surface; no market
needed the historical surface, so the historical bar field names below come from the earlier probe samples
(`samples/ov_hist_cs1.json`), not from this dump. Money fields are strings in dollars with 4 decimals
("0.0100"); counts are strings with 2 decimals ("224.85"). "Ever empty" is taken from `_report.json`
`null_counts` (missing, null, "" or {}), counted over this dump only.

## What the dump shows

- Every market is `finalized`, has `result` and `expiration_value`; no flags were raised on any market.
- Strike fields: `floor_strike` is empty in 7 markets (the "less" ones), `cap_strike` is empty in 7 markets (the "greater" ones). Both are numbers (68, 67).
- Bars are sparse: only minutes with some activity are returned. 56,899 minutes are missing between the first and last bar of the markets (e.g. KXHIGHNY-26NOV22-B67.5 has 471 bars for 2,340 open minutes). The report's `n_gaps` therefore counts normal absences, not errors.
- Bars come in four shapes: `price` with open/high/low/mean/close (traded in the minute, 42 + 12,441 bars; 12,441 also carry `previous_dollars`), `price` with only `previous_dollars` (no trade, 28,397 bars), and `price` = `{}` (278 bars, no trade and no previous price yet). A bar has a trade only when `price.close_dollars` exists.
- Bar volume equals trade volume for every market (sum of `volume_fp` over bars equals sum of `count_fp` over trades, difference below 0.5 contracts for all 42).
- No trade has `created_time` after `close_time` (`n_trades_after_close` is 0 everywhere). No block trades (`is_block_trade` is always false).
- Bars per market never exceed `open_minutes + 1`. Markets are open about 39 hours (e.g. 2026-11-21T15:00Z to 2026-11-23T06:00Z), so a market opens the day before its event day.
- `taker_side` / `taker_outcome_side` pair only as (yes, bid, yes) 36,331 times and (no, ask, no) 22,453 times (`taker_side`, `taker_book_side`, `taker_outcome_side`).

## Markets (`markets.json`)

| Field | Type | Unit / meaning | Example | Ever empty | Postgres column |
|---|---|---|---|---|---|
| ticker | string | market id | KXHIGHNY-26NOV22-B67.5 | no | ticker text primary key |
| event_ticker | string | event id | KXHIGHNY-26NOV22 | no | event_ticker text |
| market_type | string | | binary | no | market_type text |
| title | string | | Will the maximum temperature be 67-68° on Nov 22, 2026? | no | title text |
| yes_sub_title | string | | 67° to 68° | no | yes_sub_title text |
| no_sub_title | string | | 67° to 68° | no | no_sub_title text |
| strike_type | string | greater / less / between | between | no | strike_type text |
| floor_strike | number | degrees F | 67 | yes, 7 markets | floor_strike numeric |
| cap_strike | number | degrees F | 68 | yes, 7 markets | cap_strike numeric |
| custom_strike | object | `City`, `structured_city_id` | {"City": "New York City", ...} | no | custom_strike jsonb |
| status | string | | finalized | no | status text |
| result | string | yes / no | no | no | result text |
| expiration_value | string | settling temperature, F | "61.00" | no | expiration_value numeric |
| settlement_value_dollars | string | dollars | "0.0000" | no | settlement_value_cents integer |
| settlement_ts | RFC3339 | UTC | 2026-11-23T12:12:09.156152Z | no | settlement_ts timestamptz |
| settlement_timer_seconds | number | seconds | 300 | no | settlement_timer_seconds integer |
| settlement_bounds_type | string | | default | no | settlement_bounds_type text |
| open_time | RFC3339 | UTC | 2026-11-21T15:00:00Z | no | open_time timestamptz |
| close_time | RFC3339 | UTC | 2026-11-23T06:00:00Z | no | close_time timestamptz |
| created_time | RFC3339 | UTC | 2026-11-21T10:30:33.972002Z | no | created_time timestamptz |
| updated_time | RFC3339 | UTC | 2026-11-23T12:12:09.635464Z | no | updated_time timestamptz |
| occurrence_datetime | RFC3339 | UTC | 2026-11-23T15:00:00Z | no | occurrence_ts timestamptz |
| expected_expiration_time | RFC3339 | UTC | 2026-11-23T20:00:00Z | no | expected_expiration_ts timestamptz |
| latest_expiration_time | RFC3339 | UTC | 2026-11-29T15:00:00Z | no | latest_expiration_ts timestamptz |
| can_close_early | bool | | true | no | can_close_early boolean |
| early_close_condition | string | rule text | "The Last Trading Time will be ..." | no | early_close_condition text |
| rules_primary | string | rule text | "If the maximum temperature ..." | no | rules_primary text |
| rules_secondary | string | rule text | "Not all weather data is the same ..." | no | rules_secondary text |
| exchange_index | number | | 0 | no | exchange_index integer |
| price_level_structure | string | | linear_cent | no | price_level_structure text |
| price_ranges | list of objects | `start`, `end`, `step` in dollars | [{"end": "1.0000", "start": "0.0000", "step": "0.0100"}] | no | price_ranges jsonb |
| notional_value_dollars | string | dollars | "1.0000" | no | notional_value_cents integer |
| last_price_dollars | string | dollars | "0.0100" | no | last_price_cents integer |
| previous_price_dollars | string | dollars | "0.0100" | no | previous_price_cents integer |
| previous_yes_ask_dollars | string | dollars | "0.0100" | no | previous_yes_ask_cents integer |
| previous_yes_bid_dollars | string | dollars | "0.0000" | no | previous_yes_bid_cents integer |
| yes_ask_dollars | string | dollars | "1.0000" | no | yes_ask_cents integer |
| yes_bid_dollars | string | dollars | "0.0000" | no | yes_bid_cents integer |
| no_ask_dollars | string | dollars | "1.0000" | no | no_ask_cents integer |
| no_bid_dollars | string | dollars | "0.0000" | no | no_bid_cents integer |
| yes_ask_size_fp | string | contracts | "0.00" | no | yes_ask_size numeric |
| yes_bid_size_fp | string | contracts | "0.00" | no | yes_bid_size numeric |
| open_interest_fp | string | contracts | "2252.15" | no | open_interest numeric |
| volume_fp | string | contracts, whole life | "2252.15" | no | volume numeric |
| volume_24h_fp | string | contracts | "1538.64" | no | volume_24h numeric |

The last block (prices, sizes, volumes, `updated_time`) is a final snapshot of a finalized market, not history.

## Bars (`bars_1min/<ticker>.json`, `candlesticks`)

Only the live names were seen in this dump. Historical names are from the earlier probe, not this dump.
The bar has no ticker field; the file name gives it. Nested objects hold the same four or five keys.

| Live field | Historical field | Type | Unit | Example | Ever empty | Postgres column |
|---|---|---|---|---|---|---|
| end_period_ts | end_period_ts | integer | epoch seconds, end of the minute | 1795273440 | no | end_period_ts timestamptz |
| price (object) | price | object | `{}` when nothing yet traded | see below | yes, 278 bars (`{}`) | (split below) |
| price.open_dollars | price.open | string | dollars | "0.0200" | absent when no trade | price_open_cents integer null |
| price.high_dollars | price.high | string | dollars | "0.0200" | absent when no trade | price_high_cents integer null |
| price.low_dollars | price.low | string | dollars | "0.0100" | absent when no trade | price_low_cents integer null |
| price.mean_dollars | price.mean | string | dollars | "0.0104" | absent when no trade | price_mean_cents numeric null |
| price.close_dollars | price.close | string | dollars | "0.0100" | absent when no trade | price_close_cents integer null |
| price.previous_dollars | price.previous | string | dollars, last traded price before the minute | "0.1900" | absent on first bars and 42 traded bars | price_previous_cents integer null |
| yes_ask.open_dollars / high / low / close | yes_ask.open / high / low / close | string | dollars | "0.0400" | no | yes_ask_open_cents ... yes_ask_close_cents integer |
| yes_bid.open_dollars / high / low / close | yes_bid.open / high / low / close | string | dollars | "0.0200" | no | yes_bid_open_cents ... yes_bid_close_cents integer |
| volume_fp | volume | string | contracts in the minute | "265.00" | no | volume numeric |
| open_interest_fp | open_interest | string | contracts at end of the minute | "265.00" | no | open_interest numeric |

Notes: in the earlier historical sample `price.previous` can be null and no-trade bars carry nulls for
open/high/low/mean/close; live bars leave those keys out instead. Primary key suggestion: (ticker, end_period_ts).

## Trades (`trades/<ticker>.json`, `trades`)

| Field | Type | Unit / meaning | Example | Ever empty | Postgres column |
|---|---|---|---|---|---|
| trade_id | string | uuid | 072a1bc4-0751-971c-c8cf-bfd053b422f0 | no | trade_id text primary key |
| ticker | string | market id | KXHIGHNY-26NOV22-B67.5 | no | ticker text |
| created_time | RFC3339 | UTC, microseconds | 2026-11-23T04:27:42.459436Z | no | created_ts timestamptz |
| count_fp | string | contracts | "224.85" | no | count numeric |
| yes_price_dollars | string | dollars | "0.0100" | no | yes_price_cents integer |
| no_price_dollars | string | dollars, equals 1 - yes price | "0.9900" | no | no_price_cents integer |
| taker_side | string | yes / no | yes | no | taker_side text |
| taker_outcome_side | string | yes / no | yes | no | taker_outcome_side text |
| taker_book_side | string | bid / ask | bid | no | taker_book_side text |
| is_block_trade | bool | always false here | false | no | is_block_trade boolean |
