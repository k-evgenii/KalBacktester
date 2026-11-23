# Weather data probe (run 2026-11-23)

Example station KNYC (40.7790, -73.9693). Example week 2026-11-15 to 2026-11-21. Every request used `User-Agent: kalbt-probe`, no keys. Samples are in /home/kondr/KalBacktester/api_probe/weather/samples/ (35 files). The 1.7 MB deep-history responses were checked in a scratch folder and not saved. No new API requests were made for this reply; I only re-read saved files to confirm field lists.

## 1. Summary
- Forecasts: Open-Meteo Previous Runs works for 9 of 10 models, hourly, UTC. `ecmwf_aifs025` is all-null for the recent week. It has data only from 2024-03-13 to 2025-03-11, and nothing after.
- Lead coverage on the week 2026-11-15 to 2026-11-21:
  - Leads 0-7: ifs, gfs, gem, jma, nbm.
  - Leads 0-6: icon, ukmo.
  - Leads 0-3: meteofrance.
  - Leads 0-1: hrrr.
- Earliest non-null dates:
  - Lead 1 starts 2024-01-19 for icon, gem, meteofrance and hrrr.
  - Lead 1 starts 2024-02-04 for ifs, 2024-08-08 for ukmo and 2024-10-09 for nbm.
  - Lead 1 starts 2021 for gfs (2021-03-24) and jma (2021-01-01).
  - Lead 0 for gfs, jma and hrrr equals my query start (2021-01-01), so it may go earlier.
- Lead definition, docs quote as relayed by the fetch tool: "Requesting temperature_2m_previous_day1 returns the value predicted 24 hours before valid time". The docs name no run time of day. I did not check the quote against the page itself.
- Other variables work with previous-day leads: dew_point_2m, cloud_cover, wind_speed_10m, precipitation and shortwave_radiation all returned 168 of 168 values.
- Other Open-Meteo APIs:
  - Historical Forecast gave data for gfs_seamless at 2018, 2020, 2022 and 2024, and nothing for 2016.
  - Archive reaches back to at least 1940.
  - Ensemble refused a start date of 2025-01-10 (HTTP 400, allowed range 2026-08-22 to 2026-12-28), so past members are not archived long-term.
- Official max (CLI):
  - IEM `json/cli.py` is the simplest route: one row per day with `high`, `high_time` (local standard time) and a `product` id holding the issuance time. It returned data for years 2005 to 2026. Year 2000 and year 1990 returned empty.
  - IEM list plus text gives `entered` (UTC) and raw text. Two reports per day: a preliminary at about 21:40Z and a final at about 06:20Z the next day.
  - The NWS API list shows only about 7 days (14 entries) and has no parsed max.
- The IEM list `date` parameter matched the UTC date of `entered`.
- Intraday:
  - NWS observations reach back only about 7 days. Temperature is in degC to 0.1.
  - IEM ASOS hourly has data for 2000 and later (1990 returned only the header). Its `tmpf` was whole F in all 50 sample rows.
  - IEM 1-minute returned data for 2026-09-20 but only a header for four other dates tried.
- NWS forecast: only the current forecast is served. IEM keeps NWS text products (ZFPOKX, PFMOKX, AFDOKX) with past dates.
- Station metadata: networks `NY_ASOS` (id NYC), `CA_ASOS` (id LAX), `FL_ASOS` (id MIA). IEM ids have no leading K.
- Limits, docs only: Open-Meteo free tier 600 per minute, 5,000 per hour, 10,000 per day, 300,000 per month, non-commercial, data CC BY 4.0. IEM `asos.py` returned HTTP 429 once at about 1.7 requests per second.
- Changes from the old code: all six old requests still return 200. AIFS is a data gap, not an error. `asos1min.py` rejects `KNYC` (HTTP 422), so use `NYC`.

## 2. Endpoint sections

### 2.1 Open-Meteo Previous Runs
```
curl -H "User-Agent: kalbt-probe" "https://previous-runs-api.open-meteo.com/v1/forecast?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-21&hourly=temperature_2m,temperature_2m_previous_day1,temperature_2m_previous_day2,temperature_2m_previous_day3,temperature_2m_previous_day4,temperature_2m_previous_day5,temperature_2m_previous_day6,temperature_2m_previous_day7&models=ecmwf_ifs025&timezone=GMT&temperature_unit=fahrenheit"
```
Samples: `pr_<model>.json` (10 files), `pr_vars_gfs.json`, `pr_vars_ecmwf.json`.

| Field | Type | Meaning |
|---|---|---|
| latitude, longitude, elevation | number | grid cell used |
| generationtime_ms | number | server time |
| utc_offset_seconds, timezone, timezone_abbreviation | int / string | 0, GMT |
| hourly_units | object | `temperature_2m` is `°F` with `temperature_unit=fahrenheit`; `time` is iso8601 |
| hourly.time | string list | `YYYY-MM-DDTHH:MM`, hourly, 168 values for 7 days |
| hourly.temperature_2m | number list | lead 0 value |
| hourly.temperature_2m_previous_dayN | number list | lead N value, null where missing |

History depth: see Q1. Errors seen: none. Missing values come back as `null`, not an error. A 5.9-year request (2021-01-01 to 2026-11-21) returned 200 for all ten models. With one model the field names carry no model suffix.

### 2.2 IEM product list
```
curl -H "User-Agent: kalbt-probe" "https://mesonet.agron.iastate.edu/api/1/nws/afos/list.json?pil=CLINYC&date=2026-11-22"
```
Samples: `iem_list_1122.json`, `iem_list_1116.json`, `iem_list_old.json` (date 2010-06-02), `iem_pfm.json`, `iem_nbh.json`, `iem_afd_old.json`.

| Field | Type | Example | Meaning |
|---|---|---|---|
| schema | object | pandas table schema | wrapper |
| data[].index | int | 0 | row number |
| entered | string | 2026-11-22T06:18:00Z | issuance time, UTC |
| pil | string | CLINYC | product |
| product_id | string | 202611220618-KOKX-CDUS41-CLINYC | unique id |
| cccc | string | KOKX | issuing office |
| count | int | 1 | seen as 1 |
| link | string | https://mesonet.agron.iastate.edu/p.php?pid=... | web page |
| text_link | string | https://mesonet.agron.iastate.edu/api/1/nwstext/<product_id> | plain text URL |

`date=2026-11-22` returned products entered on 2026-11-22 UTC (06:18Z and 21:39Z), not the day after the event. Each of the three dates tried returned exactly 2 products. Depth: 2010-06-02 works. Older not tested. Other pils (ZFPOKX for 2024-01-15 gave 11 entries, PFMOKX 5, AFDOKX 8) use the same layout.

### 2.3 IEM product text
```
curl -H "User-Agent: kalbt-probe" "https://mesonet.agron.iastate.edu/api/1/nwstext/202611220618-KOKX-CDUS41-CLINYC"
```
Samples: `iem_cli_text_final.txt`, `iem_cli_text_prelim.txt`. Plain text. The first line is a sequence number. The line `CDUS41 KOKX 220618` carries the issuance time (DDHHMM, UTC). The final report (06:18Z, "118 AM EST") covers the full previous day and has `MAXIMUM 65  247 PM` (value, time in LST). The preliminary (21:39Z, "439 PM EST") says "VALID TODAY AS OF 0400 PM LOCAL TIME", so the day is unfinished.

### 2.4 IEM parsed CLI
```
curl -H "User-Agent: kalbt-probe" "https://mesonet.agron.iastate.edu/json/cli.py?station=KNYC&year=2026"
```
Sample: `iem_cli_2026.json` (409 KB, 326 rows, last `valid` 2026-11-22). Top level: `results` (list), `generated_at`. Row fields (53, read from the saved file):

| Field | Type | Example | Meaning |
|---|---|---|---|
| station | string | KNYC | station |
| valid | string | 2026-11-20 | climate day |
| state, wfo, name | string | NY, OKX, NEW YORK CITY | |
| link | string | /api/1/nwstext/202611210621-KOKX-CDUS41-CLINYC | text URL |
| product | string | 202611210621-KOKX-CDUS41-CLINYC | source report id (contains issuance time) |
| high | int | 68 | daily max, whole F |
| high_time | string | 310 PM | time of max, local standard time |
| high_normal, high_depart, high_record, high_record_years | number / list | 53, 15, 77, [1985] | normal, departure, record |
| low, low_time, low_normal, low_depart, low_record, low_record_years | as above | 51, 713 AM | minimum |
| precip, precip_normal, precip_month, precip_month_normal, precip_jan1, precip_jan1_normal, precip_jun1, precip_jun1_normal, precip_jul1, precip_dec1, precip_dec1_normal, precip_record, precip_record_years | number or "M" | 0.0 | precipitation, inches; "M" = missing |
| snow, snowdepth, snow_normal, snow_month, snow_jun1, snow_jul1, snow_dec1, snow_record, snow_record_years, snow_jul1_normal, snow_jul1_depart, snow_dec1_normal, snow_month_normal | number, "M" or "T" | 0.0 | snow; "T" = trace |
| average_sky_cover | number | 0.0 | |
| resultant_wind_speed, resultant_wind_direction | number or "M" | "M" | |
| highest_wind_speed, highest_wind_direction, highest_gust_speed, highest_gust_direction | number | 8, 300, 14, 280 | wind |
| average_wind_speed | number | 2.8 | |

Depth: 2005 returned 365 rows (first 2005-01-01); 2008, 2010 and 2020 returned full years. 2000 and 1990 returned `{"results": []}`. Years 2001-2004 not tested. One row per day, so preliminary and corrected versions are not separate. No issuance field other than the id inside `product`.

### 2.5 NWS API CLI
```
curl -H "User-Agent: kalbt-probe" "https://api.weather.gov/products/types/CLI/locations/NYC"
curl -H "User-Agent: kalbt-probe" "https://api.weather.gov/products/<id>"
```
Samples: `nws_cli_list.json`, `nws_cli_product.json`.
List `@graph[]`: `@id`, `id` (uuid), `wmoCollectiveId` (CDUS41), `issuingOffice` (KOKX), `issuanceTime` (2026-11-22T06:18:00+00:00), `productCode` (CLI), `productName` (Climatological Report (Daily)). 14 entries from 2026-11-16T21:42Z to 2026-11-23T06:20Z, about 7 days. The product detail adds `productText` (same text as IEM, with a leading `\n000\n`). No parsed max.

### 2.6 IEM station list
```
curl -H "User-Agent: kalbt-probe" "https://mesonet.agron.iastate.edu/geojson/network.py?network=NY_ASOS"
```
Samples: `iem_net_ny.json`, `iem_net_ca.json`, `iem_net_fl.json`. Top level: `type`, `features`, `generated_at`, `count`.

| Field | Type | Example | Meaning |
|---|---|---|---|
| id | string | NYC | IEM station id (no leading K) |
| geometry.coordinates | [lon, lat] | [-73.9692, 40.779] | degrees |
| properties.sname | string | NEW YORK CITY | name |
| elevation | number | 27.0 | metres |
| tzname | string | America/New_York | time zone |
| state, country, county, wfo | string | NY, US, New York, OKX | |
| time_domain, archive_begin, archive_end | string / null | (1943-Now), 1943-12-01, null | archive span; null end = active |
| climate_site, ncdc81, ncei91, synop, ugc_county, ugc_zone | string / int | NY5801, USW00094728, 94728 | cross-reference ids |
| online, network, sid | bool / string | true, NY_ASOS, NYC | |
| attributes | object | HAS1MIN, METAR_RESET_MINUTE, HAS_PHOUR, GHCNH_ID | flags |

### 2.7 NWS observations
```
curl -H "User-Agent: kalbt-probe" "https://api.weather.gov/stations/KNYC/observations?start=2026-11-20T00:00:00Z&end=2026-11-22T00:00:00Z&limit=200"
```
Sample: `nws_obs.json` (41 features, 2026-11-20T01:51Z to 2026-11-21T23:51Z, newest first). GeoJSON features: `id`, `type`, `geometry` (point [-73.98, 40.77]), `properties` with these keys: `@id, @type, elevation, station, stationId, stationName, timestamp, rawMessage, textDescription, icon, presentWeather, temperature, dewpoint, windDirection, windSpeed, windGust, barometricPressure, seaLevelPressure, visibility, maxTemperatureLast24Hours, minTemperatureLast24Hours, precipitationLastHour, precipitationLast3Hours, precipitationLast6Hours, relativeHumidity, windChill, heatIndex, cloudLayers`.

| Field | Type | Example | Unit |
|---|---|---|---|
| timestamp | string | 2026-11-21T23:51:00+00:00 | UTC |
| rawMessage | string | KNYC 212351Z AUTO 13005KT ... | METAR |
| temperature, dewpoint | object {unitCode, value, qualityControl} | 13.9, 8.3 | degC |
| windDirection | same | 130 | degrees |
| windSpeed, windGust | same | 9.36, null | km/h |
| barometricPressure, seaLevelPressure | same | 102740, 102650 | Pa |
| visibility | same | 16090 | m |
| precipitationLastHour / 3Hours / 6Hours | same | null | mm |
| max/minTemperatureLast24Hours | same | null | degC |

Temperature 13.9 matches the METAR T-group `T01390083`, so it is tenths of Celsius. Depth: 2026-11-17 returned 5 observations for 00-06Z. 2026-11-14, 2026-11-09, 2026-10-01 and 2026-06-01 returned an empty `features` list (HTTP 200). So about 7 days. Observations appear at :51 each hour.

### 2.8 IEM ASOS hourly
```
curl -H "User-Agent: kalbt-probe" "https://mesonet.agron.iastate.edu/cgi-bin/request/asos.py?station=NYC&data=tmpf&data=dwpf&data=relh&data=sknt&data=p01i&data=metar&year1=2026&month1=11&day1=20&year2=2026&month2=11&day2=22&tz=Etc/UTC&format=onlycomma&latlon=yes&report_type=3&report_type=4"
```
Sample: `asos_hourly.csv` (50 rows, 2026-11-20 00:51 to 2026-11-21 23:51).

| Column | Example | Unit |
|---|---|---|
| station | NYC | id |
| valid | 2026-11-20 00:51 | time in the requested tz |
| lon, lat | -73.9693, 40.7790 | degrees |
| tmpf, dwpf | 56.00, 34.00 | F |
| relh | 43.29 | percent |
| sknt | 0.00 | knots |
| p01i | 0.00 | inches |
| metar | KNYC 200051Z AUTO ... | raw METAR |

All 50 tmpf values were whole numbers (METAR `T01330011` = 13.3 C = 55.94 F shows as 56.00), so IEM tmpf is whole F, converted from Celsius and rounded (my inference from this sample). Depth: 2000-01-01 to 01-02 gave 19 data rows, 2010 gave 42, 2020 gave 24, 1990 gave the header only. `report_type=1` returned only a header for 2026-11-20. Errors: one HTTP 429 with body `Too many requests from your IP address, slow down.` at about 1.7 requests per second; a retry after 5 s returned 200.

### 2.9 IEM ASOS 1-minute
```
curl -H "User-Agent: kalbt-probe" "https://mesonet.agron.iastate.edu/cgi-bin/request/asos1min.py?station=NYC&vars=tmpf&sts=2026-09-20T00:00Z&ets=2026-09-20T01:00Z&format=comma&tz=UTC"
```
Sample: `asos_1min_2026_09.csv`. Columns: `station, station_name, valid(UTC), tmpf` (example `NYC,NEW YORK CITY,2026-09-20 00:00,66`), one row per minute, tmpf whole F. Same query for 2026-10-01 (`asos_1min_nov.csv`), 2026-11-20 (`asos_1min.csv`) and 2025-10-20 (`asos_1min_2025.csv`) returned only the header; 2026-10-20 and LAX on 2026-11-20 did the same, but those files were deleted. Reason unknown. `station=KNYC` returned HTTP 422 `Unknown station provided: KNYC200`, so use `NYC`.

### 2.10 NWS points and forecasts
```
curl -H "User-Agent: kalbt-probe" "https://api.weather.gov/points/40.7790,-73.9693"
curl -H "User-Agent: kalbt-probe" "https://api.weather.gov/gridpoints/OKX/34,45/forecast"
```
Samples: `nws_points.json`, `nws_forecast.json`, `nws_forecast_hourly.json`, `nws_gridpoints.json`.
- `/points` properties: `cwa` OKX, `gridId` OKX, `gridX` 34, `gridY` 45, `forecast`, `forecastHourly`, `forecastGridData`, `observationStations`, `forecastZone` (NYZ072), `county`, `fireWeatherZone`, `timeZone` (America/New_York), `radarStation` (KOKX), `astronomicalData`, `nwr`.
- `/forecast` (14 periods, 12 hours each). Top-level: `units, forecastGenerator, generatedAt, updateTime, validTimes, elevation`. Period fields: `number, name, startTime, endTime, isDaytime, temperature` (68, F), `temperatureUnit`, `temperatureTrend`, `probabilityOfPrecipitation` (percent), `windSpeed` (text "8 mph"), `windDirection`, `icon`, `shortForecast`, `detailedForecast`.
- `/forecast/hourly` (156 periods). Same fields plus `dewpoint` (degC) and `relativeHumidity` (percent).
- `/gridpoints/OKX/34,45`: raw grid with `temperature, dewpoint, maxTemperature, minTemperature, relativeHumidity, apparentTemperature, skyCover, windDirection, windSpeed, windGust, probabilityOfPrecipitation, quantitativePrecipitation, snowfallAmount` and others. Values are `{uom: "wmoUnit:degC", values: [{validTime: "2026-11-23T12:00:00+00:00/PT13H", value: 20}]}`.

### 2.11 Open-Meteo Historical Forecast, Ensemble, Archive
```
curl -H "User-Agent: kalbt-probe" "https://historical-forecast-api.open-meteo.com/v1/forecast?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-21&hourly=temperature_2m&models=gfs_seamless&temperature_unit=fahrenheit"
curl -H "User-Agent: kalbt-probe" "https://ensemble-api.open-meteo.com/v1/ensemble?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-16&hourly=temperature_2m&models=gfs_seamless&timezone=GMT&temperature_unit=fahrenheit"
curl -H "User-Agent: kalbt-probe" "https://archive-api.open-meteo.com/v1/archive?latitude=40.7790&longitude=-73.9693&start_date=2026-11-15&end_date=2026-11-21&hourly=temperature_2m&timezone=GMT&temperature_unit=fahrenheit"
```
Samples: `hf.json`, `hf_deep.json`, `ens.json`, `ens_past.json` (the 400 error), `arch.json`, `arch_deep.json`. Layout as in 2.1. Ensemble returns `temperature_2m` plus `temperature_2m_member01` to `member30` (31 columns, unit °F). The historical forecast request without `temperature_unit` returned °C in the deep tests. Errors: ensemble with a 2025 start date returned HTTP 400 `{"reason":"Parameter 'start_date' is out of allowed range from 2026-08-22 to 2026-12-28","error":true}`.

## 3. Answers

### Q1. Previous Runs coverage
Observed for 2026-11-15 to 2026-11-21 (168 hourly values per column; Y = all 168 non-null, - = all null). Earliest dates come from a request starting 2021-01-01 (first non-null hour of the lead 0, lead 1 and lead 7 columns). "(start)" means the value equals the query start, so data may go earlier. Time step is hourly for every model.

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

"never" means no non-null value from 2021-01-01 to 2026-11-21. Run issue time of day: the Previous Runs docs page (fetched) gives none per model. It says global models update every 6 hours and regional models every 1 to 3 hours. Leads 2-6 earliest dates were not tested separately.

### Q2. Lead meaning
Docs, as relayed by the fetch tool: "Requesting temperature_2m_previous_day1 returns the value predicted 24 hours before valid time" and "_previous_day1 is the value that was predicted 24 hours before valid time". I did not verify the wording on the page itself. The docs do not say which run (00z, 06z, 12z or 18z) supplies a lead-N value.

### Q3. Other variables
Docs list (relayed, docs only): relative humidity, dewpoint, apparent temperature, precipitation, rain, showers, snowfall, weather code, sea-level and surface pressure, total cloud cover, wind speed and direction at 10 m and higher levels, and shortwave, direct, diffuse, DNI and GTI radiation. Tested: `dew_point_2m`, `cloud_cover`, `wind_speed_10m`, `precipitation`, `shortwave_radiation` with leads 0, 1, 3 on gfs_seamless and lead 2 on ecmwf_ifs025. All returned 168 of 168 non-null values.

### Q4. Other models on this API
Docs only, not tested, names as listed by providers on the docs page: Best match; ECMWF IFS HRES 9 km and AIFS 0.25 single; CMA GRAPES Global; GFS Global 0.11 and 0.25; RRFS; HRRR (listed as a US CONUS model); NAM CONUS (discontinued 2026-11-01); AIGFS; HGEFS; JMA MSM and GSM; KMA (discontinued 2026-03-31); DWD ICON Global, EU, D2; GEM Global, Regional, HRDPS; Meteo-France ARPEGE and AROME; ItaliaMeteo ARPAE ICON 2I; MET Norway; KNMI; DMI; UK Met Office Global 10 km and UK 2 km; MeteoSwiss ICON; GeoSphere Austria; CHMI Aladin. API model-name strings were not tested.

### Q5. Other Open-Meteo APIs
- Historical Forecast API: returned 168 of 168 values for the example week. gfs_seamless returned 48 of 48 values for 2018-06-01, 2020-06-01, 2022-06-01 and 2024-06-01, and all null for 2016-01-01. Docs (relayed): built by stitching the first hours of each run; start is typically 2021 or 2022, varying by model. Lead time of the stitched values: not stated.
- Ensemble API: 31 columns (control plus 30 members) for GFS. A 2025-01-10 start was refused (allowed range 2026-08-22 to 2026-12-28), so past members are not archived long-term. The docs (relayed) say "up to three days of historical data" for members, plus longer storage of means and spreads. I did not test retrieval for dates between 2026-08-22 and now.
- Archive API: 168 of 168 values for the example week, and 48 of 48 values for 1940-01-01, so it goes back to at least 1940. It is reanalysis, not station readings.

### Q6. Limits
Docs (pricing page, relayed): free tier 600 calls per minute, 5,000 per hour, 10,000 per day, 300,000 per month (the page says monthly limits are not enforced until a later date). Non-commercial use only, no uptime guarantee. Data licence CC BY 4.0, attribution required. A rate-limited Open-Meteo response was never triggered, so its shape is unknown. The only rate-limit response seen was from IEM (HTTP 429, plain text, see 2.8).

### Q7. Official daily maximum

| | IEM list + text | IEM `cli.py` | NWS API |
|---|---|---|---|
| Fields | list metadata plus raw text | 53 parsed fields | list metadata plus `productText` |
| Daily max | in text (parse) | `high` | in text (parse) |
| Time of max | in text (`247 PM`, LST) | `high_time` | in text |
| Issuance timestamp | `entered` (UTC) | only inside the `product` id | `issuanceTime` |
| Depth | 2010 tested | first data in 2005, year 2000 empty | last 7 days |
| Corrected reports separate | 2 entries per date (prelim and final); corrections not seen | one row per day | one entry per issuance; corrections not seen |

Simplest: `json/cli.py`, one call per year, no text parsing. Use list plus text only when the preliminary report or the exact issuance time is needed.

### Q8. Intraday observations
NWS: about 7 days back, tenths of Celsius, fields in 2.7. IEM ASOS hourly: parameters `station, data, year1/month1/day1, year2/month2/day2, tz, format, latlon, report_type`; fields and units in 2.8; whole F; observations at :51; data from 2000 on in the tests. IEM 1-minute (`asos1min.py`: `station, vars, sts, ets, format, tz`): whole F, minute rows, data returned only for 2026-09-20 of the dates tried.

### Q9. NWS official forecast
See 2.10. Past-forecast archive: the NWS API serves only the current forecast. IEM keeps NWS text products by pil with past dates: ZFPOKX returned 11 products for 2024-01-15, PFMOKX 5 and AFDOKX 8 for 2026-11-16. No gridded archive was found. Whether text from 2024 can be parsed into daily highs was not tested.

### Q10. Station metadata
The IEM network endpoint returns id, `sname`, coordinates [lon, lat], `tzname`, elevation, `archive_begin`/`archive_end`, state, WFO and attributes (2.6). KNYC: id `NYC`, network `NY_ASOS`, America/New_York, (-73.9692, 40.779). KLAX: id `LAX`, network `CA_ASOS`, America/Los_Angeles, (-118.3865, 33.9382). KMIA: id `MIA`, network `FL_ASOS`, America/New_York, (-80.3169, 25.788).

## 4. Could not determine
- The time of day each Open-Meteo model run is issued. The Previous Runs docs page does not give it.
- Which model run (00z, 06z, 12z or 18z) backs each `_previous_dayN` value.
- Why `ecmwf_aifs025` has no data after 2025-03-11.
- Earliest dates for leads 2-6, and the true earliest for GFS, JMA and HRRR (my deep query started at 2021-01-01).
- The format of an Open-Meteo rate-limited response. I never triggered one.
- The first year of IEM CLI data (between 2001 and 2005; 2001-2004 not tested). IEM list and text depth before 2010 was not tested.
- How corrected CLI reports appear. None were seen.
- Why IEM 1-minute data was empty for 2025-10-20, 2026-10-01, 2026-10-20 and 2026-11-20 (and LAX on 2026-11-20) but present for 2026-09-20.
- Whether `asos.py` accepts `KNYC`, and which `data=` names exist beyond those used. `report_type=1` returned only a header.
- API model-name strings for the other models (docs only).
- Ensemble member retention between 2026-08-22 and now; docs say 3 days, the error message says a range from 2026-08-22.
- The lead time used by the Historical Forecast stitching (not stated in the docs fetch).
- CLI routes for LAX and MIA. Only their station metadata was checked.

## 5. Request count
About 80: 77 API requests (Open-Meteo about 35, IEM about 28, NWS about 14) and 4 documentation page fetches (Open-Meteo previous-runs, pricing, historical-forecast and ensemble docs). The count was tallied by hand from my commands, not logged, so it may be off by a few. One later IEM 429 retry is included.
