# Kalshi public API probe (run 2026-11-23)

Examples: settled event `KXHIGHNY-26NOV22` (markets `-T68` greater, `-T61` less, `-B67.5` between); open event `KXHIGHNY-26NOV24`. Samples are in `/home/kondr/KalBacktester/api_probe/kalshi/samples/`, trimmed to 3 items with Python because `jq` is absent. "docs only" means read in the docs but not tested. I wrote this from my session notes and did not re-read the sample files; the sample file names below are the ones I saved. No new requests were made for this reply. REPORT.md itself was not written, because the Write tool refused it.

## 1. Summary
- Base URL: both `https://api.elections.kalshi.com/trade-api/v2` and `https://external-api.kalshi.com/trade-api/v2` answer 200 with identical bodies. The docs list external-api first and call elections "also supported".
- Rate limit: the docs give no number for unauthenticated calls ("separate limits", back off on 429). Responses carry `x-ratelimit-limit: 600`, `x-ratelimit-cost` 10 (most calls) or 50 (candles, trades, events), `-remaining` and `-reset`. No 429 was seen.
- Page limits: `limit` max is 1000 on `/markets`, `/historical/markets`, `/markets/trades` and `/historical/trades`; 1001 gives 400. The series list returns all 537 in one body.
- Candles: `period_interval` accepts only 1, 60 and 1440. Max 5000 periods per request, timestamps in seconds only. Live and batch candles use `_dollars` / `_fp` string keys; historical candles use plain string keys and `previous` can be null.
- History depth for `KXHIGHNY`: the historical surface goes back to `KXHIGHNY-24OCT24`. The live surface has markets from `26SEP15` on (426 markets).
- Cutoff: `/historical/cutoff` returns `market_settled_ts` and `trades_created_ts` of 2026-09-24. The surfaces overlap: 48 markets from 26SEP15 to 26SEP22 answer on both. Try live first, then historical.
- Strike encoding: greater has `floor_strike` only, less has `cap_strike` only, between has both. The rules text is silent on an exact tie. In the data, 3 "greater" and 22 "less" markets with value equal to strike all settled `no`.
- Fees: all 537 weather series have `fee_type=quadratic` and `fee_multiplier=1`. No fee field on markets. The fee-change endpoints are empty.
- Weather series: 537 in the category (50 `KXHIGH*`, 55 `KXLOW*`, 78 `KXRAIN*`, 46 `KXSNOW*`, 308 other). `KXHIGHLAX` and `KXHIGHMIA` are still current.
- Changes from old code: `_dollars` / `_fp` strings instead of integer cents; the order book is `orderbook_fp`; status reads `active` / `finalized`.

## 2. Endpoints
All calls use `curl -s -H 'User-Agent: kalbt-probe' "$B<path>"` with `B=https://api.elections.kalshi.com/trade-api/v2`.

### /historical/cutoff
- Command: `curl -s -H 'User-Agent: kalbt-probe' "$B/historical/cutoff"`. No parameters. Sample: `cutoff_api.elections.kalshi.com.json`, `cutoff_external-api.kalshi.com.json`.

| field | type | example | meaning |
|---|---|---|---|
| market_settled_ts | string | 2026-09-24T00:00:00Z | docs: markets and candles settled before this are historical-only |
| trades_created_ts | string | 2026-09-24T00:00:00Z | docs: trades before this are historical-only |
| orders_updated_ts | string | 2026-11-09T00:00:00Z | account data |
| market_positions_last_updated_ts | string | 2026-09-24T00:00:00Z | account data |

- Quirk: the cutoff is not a hard partition (see answer 8).

### GET /markets
- Command: `$B/markets?series_ticker=KXHIGHNY&limit=200&cursor=` (empty cursor works).
- Parameters: `series_ticker`, `event_ticker`, `status` (`settled` and `open` tested), `limit` (max 1000, 1001 gives 400 `lte` validation), `cursor`.
- Pagination: the response `cursor` is empty when done. `limit=1000` returned all 426 live KXHIGHNY markets with an empty cursor.
- Response: `{markets:[...], cursor}`; the market fields are in the next table.
- Quirk: `event_ticker=KXHIGHNY-26JUN01` returns 0 markets live.
- Samples: `markets_settled.json`, `markets_open.json`, `markets_limit1000.json`, `markets_limit200_cursor.json`, `markets_limit1001.json` (400), `markets_live_old.json`.

### GET /markets/{ticker}
- Command: `$B/markets/KXHIGHNY-26NOV22-T68`, which returns `{market:{...}}`.
- Errors: 404 `not_found` for historical-only tickers.
- Samples: `market_greater_open.json`, `market_less_settled.json`, `market_between_settled.json`, `ov_live_mkt.json`.

| field | type | example | meaning |
|---|---|---|---|
| ticker | str | KXHIGHNY-26NOV22-T68 | `{event}-{T or B}{strike}` |
| event_ticker | str | KXHIGHNY-26NOV22 | parent event |
| market_type | str | binary | |
| title | str | Will the maximum temperature be >68° on Nov 22, 2026? | |
| yes_sub_title / no_sub_title | str | 69° or above | outcome text |
| strike_type | str | greater / less / between | |
| floor_strike | number | 68 | greater and between only |
| cap_strike | number | 61 | less and between only |
| custom_strike | object | {"City":"New York City","structured_city_id":"..."} | |
| status | str | active, finalized | |
| result | str | "no" / "yes" / "" while open | outcome |
| expiration_value | str | "61.00" | settling temperature (empty once: 26JUN23-T78) |
| settlement_value_dollars | str | "0.0000" | YES payout |
| settlement_ts | RFC3339 | 2026-11-23T12:12:09.156152Z | absent on open markets |
| settlement_timer_seconds | int | 300 | |
| settlement_bounds_type | str | default | |
| open_time / close_time | RFC3339 | 2026-11-21T15:00:00Z / 2026-11-23T06:00:00Z | trading window |
| expected_expiration_time / latest_expiration_time | RFC3339 | 2026-11-23T20:00:00Z / 2026-11-29T15:00:00Z | |
| occurrence_datetime | RFC3339 | 2026-11-23T15:00:00Z | |
| created_time / updated_time | RFC3339 | | |
| can_close_early, early_close_condition | bool / str | true | |
| rules_primary, rules_secondary | str | see answer 4 | rules and source |
| yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars | str | "0.0000" | top of book |
| yes_bid_size_fp, yes_ask_size_fp | str | "0.00" | size |
| last_price_dollars, previous_price_dollars, previous_yes_bid_dollars, previous_yes_ask_dollars | str | "0.0100" | |
| volume_fp, volume_24h_fp, open_interest_fp | str | "1305.87" | contracts |
| notional_value_dollars | str | "1.0000" | |
| price_level_structure, price_ranges | str, array | linear_cent, [{start 0.0000, end 1.0000, step 0.0100}] | tick size |
| exchange_index | int | 0 | |

### GET /historical/markets and /historical/markets/{ticker}
- Commands: `$B/historical/markets?event_ticker=KXHIGHNY-26JUN01` (6 markets) and `$B/historical/markets?series_ticker=KXHIGHNY&limit=1000`.
- Pagination: the 1000 result had a non-empty cursor and is newest first (26SEP22 first, 26APR09 last on page 1). Limit 1001 gives 400.
- Fields: same as the live market; all `finalized`.
- Errors: single-market 404 body is `failed_to_get_market_by_ticker:_not_found`.
- Samples: `hist_markets_event.json`, `hist_markets_series.json`, `hist_markets_series_1000.json`, `hist_markets_series_1001.json`, `ov_hist_mkt.json`, `old_hist_mkt.json`.

### GET /events and /events/{ticker}
- Command: `$B/events?series_ticker=KXHIGHNY&limit=200&with_nested_markets=true`, plus `status=settled|open`, `cursor`. The max `limit` was not tested.
- Event fields:

| field | type | example |
|---|---|---|
| event_ticker | str | KXHIGHNY-26NOV22 |
| series_ticker | str | KXHIGHNY |
| title | str | Highest temperature in New York City on Nov 22, 2026? |
| sub_title | str | On Nov 22, 2026 |
| category | str | Climate and Weather |
| collateral_return_type | str | MECNET |
| mutually_exclusive | bool | true |
| strike_date | RFC3339 | 2026-11-24T06:00:00Z |
| strike_period | str | "" |
| settlement_sources | array | [{name:"The Weather Company", url:"https://weather.com/kalshi"}] |
| product_metadata | object | {competition:"High Temperature"} |
| exchange_index | int | 0 |
| last_updated_ts | RFC3339 | |
| markets | array | only with nested markets |

- Quirk: in the single-event call, nested markets are under `event.markets`; the top-level `markets` is empty. `/events/KXHIGHNY-25JAN01` still answers 200.
- `/events/{ticker}/metadata` is cosmetic (competition, image URLs, market_details with colours).
- Samples: `events_settled.json`, `events_open.json`, `events_nested_list.json`, `event_settled.json`, `event_open_nested.json`, `event_metadata.json`.

### GET /series/{ticker} and /series
- Commands: `$B/series/KXHIGHNY` and `$B/series?category=Climate%20and%20Weather&limit=1000`.
- Series fields:

| field | type | example |
|---|---|---|
| ticker | str | KXHIGHNY |
| title | str | Highest temperature in NYC |
| category / categories | str / array | Climate and Weather |
| tags | array | ["Daily temperature"] |
| frequency | str | daily |
| fee_type | str | quadratic |
| fee_multiplier | number | 1 |
| settlement_sources | array | The Weather Company |
| contract_url, contract_terms_url | str | assets.kalshi.com PDFs |
| additional_prohibitions | array | |
| product_metadata | object | important_info markdown |
| exchange_index | int | 0 |
| last_updated_ts | RFC3339 | |

- The list returns 537 series with no `cursor` key. Sending without `limit` gives the same body.
- Samples: `series_one.json`, `series_list_weather.json`.

### Candles, live: `/series/{series}/markets/{ticker}/candlesticks`
- Command: `$B/series/KXHIGHNY/markets/KXHIGHNY-26NOV22-T68/candlesticks?start_ts=1795273200&end_ts=1795438800&period_interval=60`. All three parameters are required.
- Response: `{ticker, candlesticks:[...]}`.

| field | type | example | meaning |
|---|---|---|---|
| end_period_ts | int | 1795276800 | inclusive bar end (daily bars end 05:00 UTC) |
| price.open/high/low/close/mean/previous_dollars | str | "0.0100" | trade OHLC; `price` is `{}` with no trades |
| yes_bid.open/high/low/close_dollars | str | "0.0000" | bid OHLC |
| yes_ask.open/high/low/close_dollars | str | "0.0100" | ask OHLC |
| volume_fp | str | "60.00" | bar volume |
| open_interest_fp | str | "60.00" | |

- Errors:
  - Interval 5 gives 400 `oneof`.
  - Millisecond timestamps give 400 "timestamps must be in seconds, not milliseconds".
  - 5760 one-minute bars give 400 "max candlesticks: 5000".
  - 5001 sixty-minute bars give 400.
  - Market 26JUN01 gives 404.
- Intervals 1, 60 and 1440 give 200. 5000 one-minute bars is fine; an empty range returns an empty list.
- Samples: `cs_live_60.json`, `cs_live_1440.json`, `cs_live_1.json`, `cs_live_1_toolong.json` (2475 bars, ok), `cs_live_5.json`, `cs_live_ms.json`, `live_cs_1_5000.json`, `live_cs_1_5760.json`, `live_cs_60_wide.json`, `cs_open_60.json`, `ov_live_cs.json`.

### Candles, historical: `/historical/markets/{ticker}/candlesticks`
- Command: same parameters, ticker only in the path.
- Fields are plain keys with string values: `end_period_ts`, `open_interest`, `volume`, `price{open,high,low,close,mean,previous}` (`previous` may be null), `yes_bid{open,high,low,close}`, `yes_ask{...}`; response `{ticker, candlesticks}`.
- Intervals 1, 60, 1440 work; 5 gives 400; the 5000-period limit applies. A live-only market (26NOV22-T68) gives 404.
- Samples: `ov_hist_cs.json`, `ov_hist_cs1.json`, `ov_hist_cs1440.json`, `ov_hist_cs_toolong.json`, `ov_hist_cs_5.json`, `old_hist_cs60.json`, `old_hist_cs1440.json`, `cs_hist_same.json` (404).

### Candles, batch: `/markets/candlesticks`
- Command: `$B/markets/candlesticks?market_tickers=KXHIGHNY-26NOV22-T68,KXHIGHNY-26NOV22-T61&start_ts=1795273200&end_ts=1795438800&period_interval=60`.
- Result: 200, `{markets:[{ticker, candlesticks:[...]}]}`, with the same `_dollars` / `_fp` keys as live.
- Sample: `cs_batch.json`.

### Event candles: `/series/{s}/events/{e}/candlesticks`
- Returned 200 but `market_candlesticks` was six empty arrays, plus `adjusted_end_ts` and `market_tickers`. The cause is not understood.
- Samples: `event_candles.json`, `event_candles2.json`.

### GET /markets/trades and /historical/trades
- Commands: `$B/markets/trades?ticker=KXHIGHNY-26NOV22-T68&limit=5` and `$B/historical/trades?ticker=KXHIGHNY-24OCT24-T67&limit=5`. No auth.
- Parameters: `ticker` optional (without it, all markets), `limit` max 1000, `cursor`. Newest first.
- Fields: `trade_id` (str), `ticker`, `created_time` (RFC3339), `yes_price_dollars` ("0.0100"), `no_price_dollars` ("0.9900"), `count_fp` ("66.00"), `taker_side` ("yes"), `taker_outcome_side`, `taker_book_side` ("bid"), `is_block_trade` (bool).
- Samples: `trades_settled_mkt.json`, `trades_open_mkt.json`, `trades_all.json`, `trades_limit1000.json`, `trades_limit1001.json` (400), `trades_old_live.json`, `hist_trades.json`, `hist_trades_old.json`, `hist_trades_min.json`.

### GET /markets/{ticker}/orderbook and /markets/orderbooks
- Command: `$B/markets/KXHIGHNY-26NOV24-T56/orderbook` returns `{orderbook_fp:{yes_dollars:[[price,size],...], no_dollars:[...]}}`. Prices and sizes are strings, and only bids are returned (docs).
- A settled market returns both lists empty. `/historical/markets/{t}/orderbook` gives 404.
- Multi-book: `$B/markets/orderbooks?tickers=a&tickers=b` (repeated parameter) returns `{orderbooks:[{orderbook_fp, ticker}]}`. The comma form returns one bogus entry.
- Samples: `ob_open.json`, `ob_settled.json`, `ob_hist.json`, `ob_multi.json`, `ob_multi2.json`.

### Exchange and fee-change endpoints
- `/exchange/status` returns `{exchange_active, trading_active, intra_exchange_transfers_active, exchange_index_statuses[]}`. Sample: `exchange_status.json`.
- `/exchange/schedule` returns `{schedule:{maintenance_windows:[], standard_hours:[per-weekday open/close]}}`. Sample: `exchange_schedule.json`.
- `/series/fee_changes` returns `{series_fee_change_arr:[]}` and `/events/fee_changes` returns `{event_fee_changes:[], cursor}`; both are empty. Samples: `series_fee_changes.json`, `event_fee_changes.json`.

## 3. Answers
1. **Base URL and limits.**
   - Both URLs return 200 with the same bodies. The docs list external-api first and elections as "also supported".
   - The docs describe a token-bucket system for authenticated tiers (default cost 10) and say unauthenticated limits are separate and unnumbered.
   - Observed headers: limit 600, cost 10 or 50, remaining 540 to 550, reset 0.25. No 429.
   - Max `limit` is 1000 on markets, historical markets, trades and historical trades. The series list ignores `limit`. Events max was not tested.
2. **Discovery.**
   - `/series` lists 537 series (50 `KXHIGH*`, 55 `KXLOW*`, 78 `KXRAIN*`, 46 `KXSNOW*`, 308 other).
   - `KXHIGHLAX` is "Highest temperature in Los Angeles" and `KXHIGHMIA` is "Highest temperature in Miami"; both are current.
   - `/series/KXHIGHNY` and `/events/KXHIGHNY-26NOV22` both answer.
3. **Market fields.**
   - See the market table above.
   - Strikes: greater has `floor_strike` only; T68 means above 68, title ">68°", yes subtitle "69° or above". Less has `cap_strike` only. Between has both, inclusive (B67.5 has floor 67 and cap 68, "67° to 68°").
   - `result` is yes/no, empty while open.
   - Settlement is carried by `expiration_value`, `settlement_value_dollars` and `settlement_ts`.
   - Times are `open_time`, `close_time`, `expected_expiration_time`, `latest_expiration_time` and `occurrence_datetime`.
   - The rules name The Weather Company, station CLINYC.
   - Status values seen: `active`, `finalized`. The docs list unopened/open/closed/settled as filter values.
4. **Strike boundary.**
   - Greater: "If the maximum temperature recorded at New York City (CLINYC) for Nov 22, 2026, is greater than 68° fahrenheit according to The Weather Company, then the market resolves to Yes."
   - Less: "... is less than 61° fahrenheit according to The Weather Company, then the market resolves to Yes."
   - Between: "... is between 67-68° fahrenheit ...".
   - The text says nothing about an exact tie. In the historical data, 3 "greater" markets with value equal to `floor_strike` (e.g. `KXHIGHNY-26JUN10-T82`, 82.00) all settled `no`. 22 "less" markets with value equal to `cap_strike` (e.g. `KXHIGHNY-26SEP19-T69`, 69.00) all settled `no`. This is consistent with strict inequalities.
5. **Candles.**
   - Live and batch candles use `_dollars` / `_fp` string keys. Historical candles use plain string keys, and `previous` can be null.
   - Prices are dollars as strings (0.0100 is one cent); `_fp` fields are fixed-point contract counts.
   - Max 5000 periods per call: about 3.5 days of 1-minute bars or 208 days of hourly bars.
   - Depth for `KXHIGHNY`: the historical surface returns candles for `KXHIGHNY-24OCT24-T67`, the oldest event found. 24OCT23 and the earlier dates I tried returned no markets. The live surface works for markets from 26SEP15 on.
6. **Finer data.**
   - Trades are public on `/markets/trades` and `/historical/trades`, back to 2024-10-24 for the oldest market. Live also returned September 2026 trades.
   - The order book is public for live markets only and empty after settlement. There is no historical order book (404).
7. **Fees.**
   - Series carry `fee_type` "quadratic" and `fee_multiplier` 1 for all 537.
   - Event-level `fee_type_override` and `fee_multiplier_override` are docs only; none were seen on events.
   - Markets carry no fee field. The pages I read gave no taker-fee formula.
   - The docs' fee-rounding page says trade fee is the model fee rounded up to $0.000001, plus a rounding fee, minus a rebate.
8. **Live vs historical.**
   - The cutoff `market_settled_ts` and `trades_created_ts` are both 2026-09-24. The docs say markets, candles and trades before it are historical-only.
   - In practice live `/markets` still lists markets from 26SEP15 to 26SEP22, which settled before the cutoff, and live trades from 2026-09-23 still return.
   - The same markets also answer on the historical endpoints with identical values (48 tickers overlap). 26JUN01 is historical-only.
   - Rule: try live first, then historical.
9. **Exchange status and schedule.**
   - Both are public and return 200.
   - The schedule is 24 hours a day, with a Thursday gap of 03:00 to 05:00 (UTC, as listed). `maintenance_windows` is empty.

## 4. Weather series
All 50 `KXHIGH*` series (frequency daily unless noted):

| ticker | title | city |
|---|---|---|
| KXHIGHNY | Highest temperature in NYC | New York |
| KXHIGHNYD | Hourly Directional NYC Temperature (hourly) | New York |
| KXHIGHLAX | Highest temperature in Los Angeles | Los Angeles |
| KXHIGHMIA | Highest temperature in Miami | Miami |
| KXHIGHCHI | Highest temperature in Chicago | Chicago |
| KXHIGHPHIL | Highest temperature in Philadelphia | Philadelphia |
| KXHIGHAUS | Highest temperature in Austin | Austin |
| KXHIGHDEN | Highest temperature in Denver | Denver |
| KXHIGHHOU | Highest temperature in Houston | Houston |
| KXHIGHOU | Highest temperature in Houston | Houston |
| KXHIGHUS | High temp in United States | United States |
| KXHIGHTDAL | Dallas Maximum Temperature | Dallas |
| KXHIGHTSATX | San Antonio Daily Maximum Temperature | San Antonio |
| KXHIGHTSEA | Seattle Maximum Temperature Daily | Seattle |
| KXHIGHTEWR | Newark, NJ (EWR) Daily Max Temp | Newark |
| KXHIGHTTTN | Trenton, NJ Daily Max Temp | Trenton |
| KXHIGHTOKC | Oklahoma City Maximum High Temperature | Oklahoma City |
| KXHIGHTMIN | Minneapolis Daily High Temperature | Minneapolis |
| KXHIGHTNOLA | New Orleans Max temp Daily | New Orleans |
| KXHIGHTSFO | San Francisco High Temperature Daily | San Francisco |
| KXHIGHTSDF | HIGHEST Temperature Louisville | Louisville |
| KXHIGHTDC | Washington DC Daily Max Temp | Washington DC |
| KXHIGHTATL | Atlanta Max Temperature | Atlanta |
| KXHIGHTBOS | Boston Maximum Daily Temperature | Boston |
| KXHIGHTLV | Las Vegas Max Daily Temperature | Las Vegas |
| KXHIGHTPHX | Phoenix High Temperature Daily | Phoenix |
| KXHIGHTHOU | Daily High Temperature Houston | Houston |
| KXHIGHTKSAN | Daily High Temperature San Diego | San Diego |
| KXHIGHTSAN | High Temperature San Diego | San Diego |
| KXHIGHTEMPDEN | High temperature denver (custom) | Denver |
| KXHIGHTVHHH | Daily high temp Hong Kong | Hong Kong |
| KXHIGHTEHAM | Amsterdam Daily High Temperature | Amsterdam |
| KXHIGHTZSPD | Daily high temp Shanghai | Shanghai |
| KXHIGHTRKSI | Daily high temp Seoul | Seoul |
| KXHIGHTSBGR | Daly high temp Sao Paulo | Sao Paulo |
| KXHIGHTYSSY | Daily high temp Sydney | Sydney |
| KXHIGHTEDDF | Daily high temp Frankfurt | Frankfurt |
| KXHIGHTEGLL | Daily high temp London | London |
| KXHIGHTRJTT | Daily high temp Tokyo | Tokyo |
| KXHIGHTOMDB | Daily high temp Dubai | Dubai |
| KXHIGHTVABB | Daily high temp Mumbai | Mumbai |
| KXHIGHTCYYZ | Daily high temp Toronto | Toronto |
| KXHIGHTMMMX | Daily high temp Mexico City | Mexico City |
| KXHIGHTZBAA | Beijing Daily High Temperature | Beijing |
| KXHIGHTLFPG | Daily high temp Paris | Paris |
| KXHIGHTLSGG | Daily high temp Geneva | Geneva |
| KXHIGHTEBBR | Daily high temp Brussels | Brussels |
| KXHIGHTLTFM | Daily high temp Istanbul | Istanbul |
| KXHIGHTEDDB | Daily high temp Berlin | Berlin |
| KXHIGHTWSSS | Daily high temp Singapore | Singapore |

Other families, counts only: low temperature `KXLOW*` has 55 series, rain `KXRAIN*` has 78, snow `KXSNOW*` has 46, and the other 308 are weather-adjacent (hurricane, earthquake, ski openings and similar). All 537 have `fee_type` quadratic and `fee_multiplier` 1.

## 5. Could not determine
- The unauthenticated rate limit: the docs give no number. I saw only a 600 limit with cost 10 or 50, no 429, and no window length.
- The taker-fee formula and fee schedule, which are not in the pages I read.
- Whether events ever carry `fee_type_override` or `fee_multiplier_override`. None were seen.
- The max `limit` for `/events`; the max tickers for the batch candle call; whether the batch call accepts historical tickers.
- Why event candlesticks returned empty arrays.
- The exact oldest `KXHIGHNY` event. 24OCT24 is the oldest found by probing a date grid. Not every day below 24OCT23 was tried, and `/historical/markets` was not paged to its end.
- Whether the live surface will drop the 26SEP15 to 26SEP22 overlap as the cutoff advances.
- The full status vocabulary: only `active` and `finalized` were seen.
- Whether the non-KX `HIGHNY` series has data.
- Why `expiration_value` is empty for `KXHIGHNY-26JUN23-T78` (result yes).
- Cities other than New York for market, candle, trade and order-book behaviour: only `KXHIGHNY` was probed in depth.
- The `KXLOW*`, `KXRAIN*` and `KXSNOW*` series were counted but not probed.
- The meaning of the Thursday 03:00 to 05:00 schedule gap: its time zone is not stated in the response and I did not confirm it.

## 6. Request count
About 107 of the 500 allowed, at most 2 per second. That is 71 saved sample files, 27 date-grid probes saved outside the samples folder, 6 documentation fetches and 3 other calls. The count is estimated from file counts.
