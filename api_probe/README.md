# API reference: Kalshi and weather data

Ready-to-run commands for the public Kalshi and weather APIs used by the backtester. No API keys are needed anywhere. Every command was tested on 2026-11-23, using New York examples (Kalshi series `KXHIGHNY`, station KNYC). Full detail and saved sample responses are in `api_probe/kalshi/` (reports and `samples/`) and `api_probe/weather/` (reports and `samples/`). Where a fact is marked "docs only" it was read in documentation but not tested.

## 1. Kalshi

### Basics
- Base URL: `https://api.elections.kalshi.com/trade-api/v2`. `https://external-api.kalshi.com/trade-api/v2` returns identical bodies (the docs list it first and call elections "also supported").
- Auth: none for everything below.
- Header: all tests sent `-H 'User-Agent: kalbt-probe'`.
- Rate limit: the docs give no number for unauthenticated calls (limits are "separate", back off on 429). Responses carry `x-ratelimit-limit: 600`, `x-ratelimit-cost` 10 (most calls) or 50 (candles, trades, events), plus `-remaining` and `-reset`. No 429 was seen. The probe ran at most 2 requests per second.
- Page limits: `limit` max is 1000 on `/markets`, `/historical/markets`, `/markets/trades`, `/historical/trades`. `limit=1001` gives HTTP 400. `/series` returns all 537 weather series in one body with no cursor. The max `limit` for `/events` was not tested. Paging uses `cursor`; an empty cursor in the response means done.
- Live vs historical: the cutoff is 2026-09-24 (`/historical/cutoff`). The two surfaces overlap (48 `KXHIGHNY` markets from 26SEP15 to 26SEP22 answer on both). Rule: try live first, then historical.
- Depth for `KXHIGHNY`: live surface has markets from 26SEP15 on (426 markets). Historical surface goes back to `KXHIGHNY-24OCT24` (oldest found).

### Cookbook

| I want... | Command | What comes back |
|---|---|---|
| All live markets of a series | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/markets?series_ticker=KXHIGHNY&limit=1000'` | `{markets:[...], cursor}`. `limit=1000` returned all 426 live KXHIGHNY markets with an empty cursor. Add `status=settled` or `status=open` to filter. Page with `&cursor=<value>`. |
| One market with its strike and result | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/markets/KXHIGHNY-26NOV22-T68'` | `{market:{...}}`. Key fields: `strike_type` (str: greater/less/between), `floor_strike` / `cap_strike` (number, degrees F), `result` (str "yes"/"no", "" while open), `expiration_value` (str, settling temperature e.g. "61.00"), `settlement_value_dollars` (str, YES payout), `settlement_ts`, `open_time`, `close_time` (RFC3339), `status` (`active` / `finalized`), `yes_bid_dollars`, `yes_ask_dollars`, `last_price_dollars` (str, dollars), `volume_fp`, `open_interest_fp` (str, contracts). 404 for historical-only tickers. |
| Markets older than the live window | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/historical/markets?series_ticker=KXHIGHNY&limit=1000'` | Same market fields, all `finalized`. Newest first (26SEP22 first on page 1); response cursor is non-empty, keep paging. |
| Historical markets of one event | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/historical/markets?event_ticker=KXHIGHNY-26JUN01'` | 6 markets. (`event_ticker=KXHIGHNY-26JUN01` on live `/markets` returns 0.) |
| One historical market | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/historical/markets/<TICKER>'` | `{market:{...}}`, same fields. 404 body `failed_to_get_market_by_ticker:_not_found` if the ticker is live-only. |
| Events of a series (with nested markets) | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/events?series_ticker=KXHIGHNY&limit=200&with_nested_markets=true'` | Events: `event_ticker`, `series_ticker`, `title`, `strike_date` (RFC3339), `mutually_exclusive` (bool), `settlement_sources` (array), `markets` (array, only with nested markets). Optional `status=settled\|open`, `cursor`. In the single-event call `/events/{ticker}`, nested markets sit under `event.markets`. |
| Hourly candles for a live market | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/series/KXHIGHNY/markets/KXHIGHNY-26NOV22-T68/candlesticks?start_ts=1795273200&end_ts=1795438800&period_interval=60'` | `{ticker, candlesticks:[...]}`. Per bar: `end_period_ts` (int, seconds, inclusive bar end; daily bars end 05:00 UTC), `price.open/high/low/close/mean/previous_dollars` (str, dollars; `price` is `{}` when no trades), `yes_bid.open/high/low/close_dollars`, `yes_ask.open/high/low/close_dollars` (str), `volume_fp`, `open_interest_fp` (str, contracts). All three of `start_ts`, `end_ts`, `period_interval` are required. |
| Candles for a historical market | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/historical/markets/<TICKER>/candlesticks?start_ts=<seconds>&end_ts=<seconds>&period_interval=60'` | `{ticker, candlesticks:[...]}` with plain keys (no `_dollars`/`_fp`): `end_period_ts`, `open_interest`, `volume`, `price{open,high,low,close,mean,previous}` (str; `previous` may be null), `yes_bid{...}`, `yes_ask{...}`. Same params and limits as live. A live-only ticker gives 404. (The report names the oldest ticker with data, `KXHIGHNY-24OCT24-T67`; the exact ticker used in the candle samples was not recorded.) |
| Candles for several markets in one call | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/markets/candlesticks?market_tickers=KXHIGHNY-26NOV22-T68,KXHIGHNY-26NOV22-T61&start_ts=1795273200&end_ts=1795438800&period_interval=60'` | `{markets:[{ticker, candlesticks:[...]}]}` with the same `_dollars` / `_fp` keys as live candles. Whether historical tickers work here was not tested. |
| Public trades of a live market | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/markets/trades?ticker=KXHIGHNY-26NOV22-T68&limit=5'` | Newest first. Per trade: `trade_id` (str), `ticker`, `created_time` (RFC3339), `yes_price_dollars` ("0.0100"), `no_price_dollars` ("0.9900") (str, dollars), `count_fp` (str, contracts), `taker_side` ("yes"), `taker_book_side` ("bid"), `is_block_trade` (bool). Without `ticker`, returns trades of all markets. |
| Public trades of a historical market | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/historical/trades?ticker=KXHIGHNY-24OCT24-T67&limit=5'` | Same trade fields. Back to 2024-10-24 for the oldest market. |
| Order book of an open market | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/markets/KXHIGHNY-26NOV24-T56/orderbook'` | `{orderbook_fp:{yes_dollars:[[price,size],...], no_dollars:[...]}}`. Prices and sizes are strings; bids only (docs). Empty lists after settlement. |
| Order books of several open markets | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/markets/orderbooks?tickers=<A>&tickers=<B>'` | `{orderbooks:[{orderbook_fp, ticker}]}`. Repeat the `tickers` parameter; a comma-separated list returns one bogus entry. |
| List of all weather series | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/series?category=Climate%20and%20Weather&limit=1000'` | 537 series, no cursor. Per series: `ticker`, `title`, `frequency`, `tags`, `fee_type` (quadratic), `fee_multiplier` (1), `settlement_sources`. Counts: 50 `KXHIGH*`, 55 `KXLOW*`, 78 `KXRAIN*`, 46 `KXSNOW*`, 308 other. |
| One series | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/series/KXHIGHNY'` | Series fields as above (`title` "Highest temperature in NYC", `frequency` daily). |
| Cutoff between live and historical | `curl -s -H 'User-Agent: kalbt-probe' 'https://api.elections.kalshi.com/trade-api/v2/historical/cutoff'` | `market_settled_ts`, `trades_created_ts` (str, both 2026-09-24T00:00:00Z when tested), plus account fields. |

Other public calls that answered 200: `/exchange/status`, `/exchange/schedule` (24 hours, Thursday gap 03:00 to 05:00 as listed, time zone unconfirmed), `/series/fee_changes` and `/events/fee_changes` (both empty).

### Pitfalls
- Prices are dollar strings ("0.0100" is one cent), not integer cents. Counts are fixed-point strings (`_fp`). Convert before use.
- Field names differ between live/batch candles (`price.close_dollars`, `volume_fp`, `open_interest_fp`) and historical candles (`price.close`, `volume`, `open_interest`, `previous` may be null).
- `period_interval` accepts only 1, 60 and 1440 (5 gives 400). Timestamps must be seconds; milliseconds give 400.
- Max 5000 periods per candle request (5760 one-minute bars gives 400 "max candlesticks: 5000"). That is about 3.5 days of 1-minute bars or 208 days of hourly bars. An empty range returns an empty list.
- Live first, then historical: the cutoff is not a hard partition. Live still serves 26SEP15 to 26SEP22, and the same markets answer on both with identical values. 26JUN01 is historical-only (live gives 404).
- Order books exist for live markets only; there is no historical order book (404), and a settled market returns empty lists.
- In `/events/{ticker}`, nested markets are under `event.markets`; the top-level `markets` is empty.
- Event candlesticks (`/series/{s}/events/{e}/candlesticks`) returned 200 with six empty arrays; do not rely on it (cause not understood).
- `expiration_value` can be empty even on a settled market (`KXHIGHNY-26JUN23-T78`, result yes).
- Status values seen: `active`, `finalized`. The docs also list unopened/open/closed/settled as filter values (docs only).
- Fees: series carry `fee_type` quadratic, `fee_multiplier` 1; markets carry no fee field; the taker-fee formula was not found. Docs only: the trade fee is the model fee rounded up to $0.000001, plus a rounding fee, minus a rebate; events may carry `fee_type_override` and `fee_multiplier_override` (none seen).
- Changes from old code: `_dollars` / `_fp` strings instead of integer cents; the order book key is `orderbook_fp`.

## 2. Open-Meteo

### Basics
- Base URLs (all GET, no auth): `https://previous-runs-api.open-meteo.com/v1/forecast`, `https://historical-forecast-api.open-meteo.com/v1/forecast`, `https://ensemble-api.open-meteo.com/v1/ensemble`, `https://archive-api.open-meteo.com/v1/archive`.
- Header: all tests sent `User-Agent: kalbt-probe`.
- Limits (docs only, pricing page): free tier 600 calls per minute, 5,000 per hour, 10,000 per day, 300,000 per month (monthly not yet enforced per the page); non-commercial use only; data CC BY 4.0, attribution required. No Open-Meteo rate-limit response was ever triggered, so its shape is unknown.
- Hourly data, `timezone=GMT`. Missing values come back as `null`, not as an error. `temperature_unit=fahrenheit` gives °F. A 5.9-year request (2021-01-01 to 2026-11-21) returned 200 for all ten models.
- With one model in `models=`, field names carry no model suffix.
- Response layout: `latitude`, `longitude`, `elevation`, `utc_offset_seconds`, `timezone`, `hourly_units`, `hourly.time` (strings `YYYY-MM-DDTHH:MM`, 168 values for 7 days), then one list per variable.

### Cookbook

| I want... | Command | What comes back |
|---|---|---|
| Forecast for one model at leads 0-7 | `curl -H 'User-Agent: kalbt-probe' 'https://previous-runs-api.open-meteo.com/v1/forecast?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-21&hourly=temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2,temperature_2m_previous_day3,temperature_2m_previous_day4,temperature_2m_previous_day5,temperature_2m_previous_day6,temperature_2m_previous_day7&models=ecmwf_ifs025&timezone=GMT&temperature_unit=fahrenheit'` | `hourly.temperature_2m` (list of number, °F, lead 0 value) and `hourly.temperature_2m_previous_dayN` (lead N, number or null). 168 values per list for 7 days. Change `models=` to another model name from the coverage table. |
| Other forecast variables with leads | Same URL with `hourly=dew_point_2m,dew_point_2m_previous_day1,dew_point_2m_previous_day3&models=gfs_seamless` (swap the variable name for `cloud_cover`, `wind_speed_10m`, `precipitation`, `shortwave_radiation`) | Same layout, one list per requested name. These five variables were tested with leads 0, 1, 3 on `gfs_seamless` and lead 2 on `ecmwf_ifs025`; all returned 168 of 168 non-null values. Command assembled from the tested pattern above. |
| Ensemble members | `curl -H 'User-Agent: kalbt-probe' 'https://ensemble-api.open-meteo.com/v1/ensemble?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-16&hourly=temperature_2m&models=gfs_seamless&timezone=GMT&temperature_unit=fahrenheit'` | `temperature_2m` plus `temperature_2m_member01` to `member30` (31 lists, °F). Only recent dates are accepted (see pitfalls). |
| Reanalysis (archive) | `curl -H 'User-Agent: kalbt-probe' 'https://archive-api.open-meteo.com/v1/archive?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-21&hourly=temperature_2m&timezone=GMT&temperature_unit=fahrenheit'` | `hourly.temperature_2m` (°F). Reanalysis, not station readings. Returned 48 of 48 values for 1940-01-01, so it reaches back to at least 1940. |
| Stitched historical forecast | `curl -H 'User-Agent: kalbt-probe' 'https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-21&hourly=temperature_2m&models=gfs_seamless&temperature_unit=fahrenheit'` | `hourly.temperature_2m`. 168 of 168 for the example week. `gfs_seamless` returned data for 2018, 2020, 2022 and 2024 test dates and all null for 2016-01-01. Without `temperature_unit` the deep tests returned °C. Lead time of the stitched values is not stated. |

### Pitfalls
- `ecmwf_aifs025` is all-null for the recent week. It has data only from 2024-03-13 to 2025-03-11 and nothing after. A null column is a data gap, not an error.
- Models differ in how many leads exist (see the coverage table): `gfs_hrrr` only leads 0-1, `meteofrance_seamless` 0-3, `icon_seamless` and `ukmo_seamless` 0-6. Missing leads are nulls.
- Lead definition (docs quote, relayed by the fetch tool, not checked on the page): "Requesting temperature_2m_previous_day1 returns the value predicted 24 hours before valid time". The docs do not say which run (00z, 06z, 12z, 18z) supplies a lead-N value, nor the run issue time per model.
- The ensemble API refuses past starts: a 2025-01-10 start gave HTTP 400 `Parameter 'start_date' is out of allowed range from 2026-08-22 to 2026-12-28`. Members are not archived long-term. Docs (relayed) say "up to three days of historical data" for members; the range between 2026-08-22 and now was not tested.
- Units default to °C unless `temperature_unit=fahrenheit` is passed (seen on the historical-forecast API).
- Docs only: other models exist on these APIs (e.g. NAM CONUS, discontinued 2026-11-01; KMA, discontinued 2026-03-31) but their model-name strings were not tested.

## 3. IEM (Iowa Environmental Mesonet)

### Basics
- Base URL: `https://mesonet.agron.iastate.edu`. No auth. Header used: `User-Agent: kalbt-probe`.
- Rate limit: no documented number. `asos.py` returned HTTP 429 once at about 1.7 requests per second, plain text body `Too many requests from your IP address, slow down.`; a retry after 5 s returned 200.
- IEM station ids have no leading K: use `NYC`, not `KNYC`. The CLI parsed endpoint takes `station=KNYC`.

### Cookbook

| I want... | Command | What comes back |
|---|---|---|
| Official daily max for a year (simplest) | `curl -H 'User-Agent: kalbt-probe' 'https://mesonet.agron.iastate.edu/json/cli.py?station=KNYC&year=2026'` | `{results:[...], generated_at}`, one row per day, 53 fields. Key fields: `valid` (str, climate day), `high` (int, whole F), `high_time` (str, local standard time, e.g. "310 PM"), `low`, `low_time`, `precip` (inches, "M" = missing), `snow` ("T" = trace), `product` (str, source report id containing the issuance time, e.g. `202611210621-KOKX-CDUS41-CLINYC`), `link` (text URL). |
| List of CLI reports for a date | `curl -H 'User-Agent: kalbt-probe' 'https://mesonet.agron.iastate.edu/api/1/nws/afos/list.json?pil=CLINYC&date=2026-11-22'` | `{schema, data:[...]}`. Per entry: `entered` (str, UTC issuance time, e.g. 2026-11-22T06:18:00Z), `pil`, `product_id` (str), `cccc` (issuing office, KOKX), `link`, `text_link`. Exactly 2 entries per date tested (about 06:18Z and 21:39Z). |
| CLI report text with issuance time | `curl -H 'User-Agent: kalbt-probe' 'https://mesonet.agron.iastate.edu/api/1/nwstext/202611220618-KOKX-CDUS41-CLINYC'` | Plain text. The line `CDUS41 KOKX 220618` carries issuance time (DDHHMM, UTC). The final report (06:18Z) has `MAXIMUM 65  247 PM` (value F, time in LST). The preliminary (21:39Z) is marked "VALID TODAY AS OF 0400 PM LOCAL TIME". |
| Hourly station observations | `curl -H 'User-Agent: kalbt-probe' 'https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=NYC&data=tmpf&data=dwpf&data=relh&data=sknt&data=p01i&data=metar&year1=2026&month1=11&day1=20&year2=2026&month2=11&day2=22&tz=Etc/UTC&format=onlycomma&latlon=yes&report_type=3&report_type=4'` | CSV: `station`, `valid` (time in requested tz), `lon`, `lat` (degrees), `tmpf`, `dwpf` (F, whole numbers), `relh` (percent), `sknt` (knots), `p01i` (inches), `metar` (raw). Observations at :51. Sample had 50 rows for 2026-11-20 to 2026-11-21. |
| 1-minute observations | `curl -H 'User-Agent: kalbt-probe' 'https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?station=NYC&vars=tmpf&sts=2026-09-20T00:00Z&ets=2026-09-20T01:00Z&format=comma&tz=UTC'` | CSV: `station`, `station_name`, `valid(UTC)`, `tmpf` (whole F), one row per minute. |
| Station metadata | `curl -H 'User-Agent: kalbt-probe' 'https://mesonet.agron.iastate.edu/geojson/network.py?network=NY_ASOS'` | GeoJSON: `type`, `features`, `generated_at`, `count`. Per feature: `id` ("NYC"), `geometry.coordinates` ([lon, lat]), `properties.sname`, `elevation` (m), `tzname`, `state`, `wfo`, `archive_begin`, `archive_end` (null = active), cross-reference ids (`climate_site`, `ncdc81`, `ncei91`), `attributes` flags. Use `CA_ASOS` and `FL_ASOS` for LAX and MIA. |
| Other NWS text products (past dates) | Same list command with another `pil`, e.g. `...afos/list.json?pil=ZFPOKX&date=2024-01-15` | Same layout. ZFPOKX for 2024-01-15 gave 11 entries; PFMOKX and AFDOKX also work. Whether 2024 text can be parsed into daily highs was not tested. |

### Pitfalls
- The list `date` parameter matches the UTC date of `entered`, not the event day. `date=2026-11-22` returns the 06:18Z report (final for 11-21) and the 21:39Z preliminary for 11-22.
- Two CLI reports per day: a preliminary at about 21:40Z (day unfinished) and a final at about 06:20Z the next day (full previous day). `cli.py` returns one row per day, so preliminary and corrected versions are not separate, and the issuance time exists only inside the `product` id.
- IEM `tmpf` is whole degrees F in all samples (converted from tenths of C and rounded; the report calls this an inference from the sample).
- Station ids have no leading K. `asos1min.py` with `station=KNYC` returned HTTP 422 `Unknown station provided`; use `NYC`. Whether `asos.py` accepts `KNYC` was not tested.
- `asos1min.py` returned data for 2026-09-20 but only the header for 2025-10-20, 2026-10-01, 2026-10-20 and 2026-11-20 (reason unknown). Do not count on 1-minute coverage.
- `asos.py`: `report_type=1` returned only a header for 2026-11-20; use 3 and 4. 1990 returned only the header; 2000, 2010, 2020 returned data.
- IEM 429: pace requests (the 429 came at about 1.7 requests per second).
- Depth: `cli.py` returned data for 2005 through 2026 (2005: 365 rows, first 2005-01-01); 2000 and 1990 returned `{"results": []}`; 2001-2004 not tested. The list endpoint worked back to 2010-06-02; older not tested.

## 4. NWS (api.weather.gov)

### Basics
- Base URL: `https://api.weather.gov`. No auth. Header used: `User-Agent: kalbt-probe`.
- Rate limits: none observed (about 14 requests made).
- Station ids here keep the K: `KNYC`.

### Cookbook

| I want... | Command | What comes back |
|---|---|---|
| Current NWS forecast (12-hour periods) | `curl -H 'User-Agent: kalbt-probe' 'https://api.weather.gov/gridpoints/OKX/34,45/forecast'` | `units`, `generatedAt`, `updateTime`, 14 `periods`. Per period: `name`, `startTime`, `endTime`, `isDaytime`, `temperature` (int, F), `temperatureUnit`, `probabilityOfPrecipitation` (percent), `windSpeed` (text, "8 mph"), `shortForecast`, `detailedForecast`. |
| Hourly NWS forecast | `curl -H 'User-Agent: kalbt-probe' 'https://api.weather.gov/gridpoints/OKX/34,45/forecast/hourly'` | 156 periods with the same fields plus `dewpoint` (degC) and `relativeHumidity` (percent). |
| Raw forecast grid | `curl -H 'User-Agent: kalbt-probe' 'https://api.weather.gov/gridpoints/OKX/34,45'` | Variables such as `temperature`, `maxTemperature`, `minTemperature`, `dewpoint`, `skyCover`, `windSpeed`, `quantitativePrecipitation`, each `{uom: "wmoUnit:degC", values:[{validTime: "2026-11-23T12:00:00+00:00/PT13H", value: 20}]}`. |
| Find grid for a coordinate | `curl -H 'User-Agent: kalbt-probe' 'https://api.weather.gov/points/40.7790,-73.9693'` | `gridId` OKX, `gridX` 34, `gridY` 45, URLs for `forecast`, `forecastHourly`, `forecastGridData`, `observationStations`, plus `timeZone` America/New_York, `radarStation` KOKX. |
| CLI report list (about last 7 days) | `curl -H 'User-Agent: kalbt-probe' 'https://api.weather.gov/products/types/CLI/locations/NYC'` | `@graph[]` of 14 entries. Per entry: `id` (uuid), `wmoCollectiveId` (CDUS41), `issuingOffice` (KOKX), `issuanceTime` (e.g. 2026-11-22T06:18:00+00:00), `productCode`, `productName`. No parsed max. |
| CLI report text | `curl -H 'User-Agent: kalbt-probe' 'https://api.weather.gov/products/<id>'` | Same entry fields plus `productText` (same text as IEM, with a leading `\n000\n`). |
| Hourly station observations (about last 7 days) | `curl -H 'User-Agent: kalbt-probe' 'https://api.weather.gov/stations/KNYC/observations?start=2026-11-20T00:00:00Z&end=2026-11-22T00:00:00Z&limit=200'` | GeoJSON `features`, newest first, one per hour at :51. In `properties`: `timestamp` (UTC), `rawMessage` (METAR), `temperature`, `dewpoint` (`{unitCode, value, qualityControl}`, degC, tenths, e.g. 13.9), `windSpeed`, `windGust` (km/h), `windDirection` (degrees), `barometricPressure`, `seaLevelPressure` (Pa), `visibility` (m), `precipitationLastHour` (mm). |

### Pitfalls
- Observations and CLI list reach back only about 7 days. Older observation dates (2026-11-14, 2026-11-09, 2026-10-01, 2026-06-01) returned HTTP 200 with an empty `features` list, not an error.
- Only the current forecast is served; there is no archive of past forecasts. IEM keeps NWS text products with past dates (section 3).
- Units differ from IEM: temperature in degC to 0.1, wind in km/h, pressure in Pa.
- The CLI product text carries the max (parse it); there is no parsed max field.

## 5. Reference tables

### Forecast model x lead coverage (Open-Meteo Previous Runs)
Week 2026-11-15 to 2026-11-21, hourly, 168 values per column. Y = all non-null, - = all null. Earliest dates come from a request starting 2021-01-01; "(start)" means the value equals the query start, so data may go earlier. "never" = no non-null value from 2021-01-01 to 2026-11-21. Leads 2-6 earliest dates were not tested.

| Model | L0 | L1 | L2 | L3 | L4 | L5 | L6 | L7 | Earliest L0 | Earliest L1 | Earliest L7 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| ecmwf_ifs025 | Y | Y | Y | Y | Y | Y | Y | Y | 2024-02-03 | 2024-02-04 | 2024-02-10 |
| ecmwf_aifs025 | - | - | - | - | - | - | - | - | 2024-03-13 (last data 2025-03-11) | 2024-03-14 | 2024-03-20 |
| gfs_seamless | Y | Y | Y | Y | Y | Y | Y | Y | 2021-01-01 (start) | 2021-03-24 | 2021-03-30 |
| icon_seamless | Y | Y | Y | Y | Y | Y | Y | - | 2022-11-16 | 2024-01-19 | never |
| gem_seamless | Y | Y | Y | Y | Y | Y | Y | Y | 2022-11-23 | 2024-01-19 | 2024-01-25 |
| jma_seamless | Y | Y | Y | Y | Y | Y | Y | Y | 2021-01-01 (start) | 2021-01-01 (start) | 2021-01-08 |
| meteofrance_seamless | Y | Y | Y | Y | - | - | - | - | 2023-12-27 | 2024-01-19 | never |
| ukmo_seamless | Y | Y | Y | Y | Y | Y | Y | - | 2022-03-01 | 2024-08-08 | never |
| ncep_nbm_conus | Y | Y | Y | Y | Y | Y | Y | Y | 2024-10-08 | 2024-10-09 | 2024-10-15 |
| gfs_hrrr | Y | Y | - | - | - | - | - | - | 2021-01-01 (start) | 2024-01-19 | never |

Summary: leads 0-7 for ifs, gfs, gem, jma, nbm; 0-6 for icon, ukmo; 0-3 for meteofrance; 0-1 for hrrr; aifs none.

### Kalshi strike encoding
Example event `KXHIGHNY-26NOV22` (rules text names The Weather Company, station CLINYC).

| strike_type | Ticker suffix | Fields | Meaning (example) |
|---|---|---|---|
| greater | `-T68` | `floor_strike` only (68) | YES if max is greater than 68 F. Title ">68°", yes subtitle "69° or above". |
| less | `-T61` | `cap_strike` only (61) | YES if max is less than 61 F. |
| between | `-B67.5` | `floor_strike` 67 and `cap_strike` 68 | YES if max is between 67 and 68 F, inclusive ("67° to 68°"). |

Exact tie: the rules text says nothing about a value equal to the strike. In the historical data, 3 "greater" markets with value equal to `floor_strike` (e.g. `KXHIGHNY-26JUN10-T82`, 82.00) all settled `no`, and 22 "less" markets with value equal to `cap_strike` (e.g. `KXHIGHNY-26SEP19-T69`, 69.00) all settled `no`. This is consistent with strict inequalities.

### Example stations

| Station | IEM id | Kalshi series | CLI product | Lat, lon (IEM) | IEM network | Time zone |
|---|---|---|---|---|---|---|
| KNYC (New York Central Park) | NYC | KXHIGHNY | CLINYC (office KOKX, WMO CDUS41) | 40.779, -73.9692 (NWS point used: 40.7790, -73.9693) | NY_ASOS | America/New_York |
| KLAX (Los Angeles) | LAX | KXHIGHLAX | not checked | 33.9382, -118.3865 | CA_ASOS | America/Los_Angeles |
| KMIA (Miami) | MIA | KXHIGHMIA | not checked | 25.788, -80.3169 | FL_ASOS | America/New_York |

KNYC elevation is 27.0 m; IEM archive begins 1943-12-01 and is active.

## 6. Not available
- Kalshi: no historical order book (404). Live order books are empty after settlement. No fee field on markets; no taker-fee formula found; fee-change endpoints are empty.
- Kalshi: event candlesticks returned only empty arrays.
- Open-Meteo: `ecmwf_aifs025` has no data after 2025-03-11. Ensemble members before 2026-08-22 are refused. Historical Forecast returned nothing for 2016.
- NWS: no archive of past forecasts (only the current one is served). Observations and the CLI list cover only about 7 days. No parsed daily max.
- IEM: `cli.py` is empty for 2000 and 1990. `asos.py` returns only a header for 1990. 1-minute data came back empty for most dates tried (all but 2026-09-20). No gridded forecast archive was found.
- Not found at all in the probes: any Kalshi rate-limit number for unauthenticated calls, and any record of corrected CLI reports.

## 7. Open questions
- Kalshi unauthenticated rate limit (docs give no number; no 429 seen) and window length.
- Kalshi taker-fee formula and schedule; whether events ever carry fee overrides.
- Kalshi `/events` max `limit`; batch-candle max tickers and whether it accepts historical tickers; why event candles are empty.
- Exact oldest `KXHIGHNY` event (24OCT24 is the oldest found; not every earlier day tried, `/historical/markets` not paged to its end). Whether the live surface will drop the 26SEP15 to 26SEP22 overlap as the cutoff advances.
- Full Kalshi status vocabulary; why `expiration_value` is empty for `KXHIGHNY-26JUN23-T78`; time zone of the Thursday schedule gap.
- Kalshi behaviour for cities other than New York, and for `KXLOW*`, `KXRAIN*`, `KXSNOW*` (counted, not probed); whether the non-KX `HIGHNY` series has data.
- Which model run (00z/06z/12z/18z) backs each Open-Meteo `_previous_dayN` value, and each model's run issue time.
- Why `ecmwf_aifs025` stops at 2025-03-11; earliest dates for leads 2-6 and true earliest for GFS, JMA, HRRR.
- Open-Meteo rate-limited response shape; ensemble member retention between 2026-08-22 and now; lead time used by Historical Forecast stitching; model-name strings for other models (docs only).
- First year of IEM CLI data (2001-2004 untested); IEM list and text depth before 2010; how corrected CLI reports appear.
- Why IEM 1-minute data is empty for most dates; which `data=` names `asos.py` accepts beyond those used; whether `asos.py` accepts `KNYC`.
- CLI routes for LAX and MIA (only station metadata was checked); whether 2024 NWS text products can be parsed into daily highs.

## 8. Settlement check (lead, 2026-11-23)
Kalshi's rules name The Weather Company as the settlement source. Compared against the IEM
`cli.py` daily high for KNYC: Kalshi `expiration_value` equalled the CLI `high` on 490 of 491
`KXHIGHNY` event days that had both values (493 events from the 3,000 most recent historical
markets plus the live window; 2 had no CLI row). The one difference: 2025-06-18, Kalshi 84,
CLI 83. All markets of an event carried the same `expiration_value`.
