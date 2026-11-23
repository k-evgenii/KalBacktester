# db/

The reconciliation database: an empty PostgreSQL 16 database for trade reconciliation, created from one file, `schema.sql`. You fill it from JSON files. There is no loader or ingestion code here.

## Contents

| File | Description |
|---|---|
| `schema.sql` | Creates the 10 tables, the three guard functions, the triggers and a `COMMENT ON` for every table and column |

The container is defined in `../docker-compose.yml` (repo root). The tests are in `../tests/db/test_schema.sql`. The spec is `../.claude/specs/001-recon-schema.md`.

## Tables

```
underlying -> series -> event -> market -> candle
run -> order_intent -> order_outcome
run -> settlement          (also references market)
run -> position_snapshot   (also references market)
```

`order_intent` also references `market`.

| Table | One row is | Key | Append-only |
|---|---|---|---|
| `underlying` | A measured quantity at a location that markets settle on (e.g. a city temperature) | `underlying_id` | no |
| `series` | A Kalshi series: a recurring family of events on one underlying | `ticker` | no |
| `event` | A Kalshi event: one underlying on one calendar day | `ticker` | no |
| `market` | A Kalshi yes/no contract on a strike within an event | `ticker` | no (settled rows are frozen, see Market guard) |
| `candle` | A quote bar (bid, ask, volume) for one market at a bar end time | `(market_ticker, ts)` | yes |
| `run` | One backtest run (strategy, params, code version, cash, date range, status) | `run_id` (generated) | no, see run guard |
| `order_intent` | An order the strategy decided to place | `intent_id` (generated) | yes |
| `order_outcome` | What happened to an intent: filled, rejected or blocked | `outcome_id` (generated) | yes |
| `settlement` | The settlement of a run's position in a market | `(run_id, market_ticker)` | yes |
| `position_snapshot` | End-of-day open position of a run in a market | `(run_id, as_of_date, market_ticker)` | yes |

## Rules the database enforces

**CHECK constraints**

| Table | Rule |
|---|---|
| `market` | `result` is null, `'yes'` or `'no'`; `result` and `settled_at` are both null or both set; `floor_strike <= cap_strike` when both are set; `open_time < close_time` |
| `candle` | `yes_bid` and `yes_ask` are null or strictly between 0 and 1; `volume >= 0`. No `bid <= ask` check (crossed quotes can occur in real data) |
| `run` | `status` in `running`, `done`, `failed`, `discarded`; `starting_cash > 0`; `date_start <= date_end`; `finished_at` is null exactly when `status = 'running'` |
| `order_intent` | `side` in `yes`, `no`; `action` in `buy`, `sell`; `quantity > 0` |
| `order_outcome` | `outcome` in `filled`, `rejected`, `blocked`; constraint `outcome_fields` (below) |
| `settlement` | `result` in `yes`, `no`; `net_units <> 0`; `payout >= 0` |
| `position_snapshot` | `net_units <> 0`; `avg_cost` strictly between 0 and 1 |

**`outcome_fields`** on `order_outcome`:
- `filled`: `quantity > 0`, `price` set and strictly between 0 and 1, `fee` set and `>= 0`.
- `rejected` or `blocked`: `quantity = 0`, `reason_code` set, `price` and `fee` null.

**Meaning of the outcomes**
- `blocked`: our own rules stopped the order before the market (trading gate locked, untrusted settlement day, risk or size limit).
- `rejected`: it reached the market but could not execute (no quote, volume cap, not enough cash, nothing to sell).
- `filled`: it executed.

**`net_units` sign convention** (`settlement`, `position_snapshot`): signed, in YES units. Positive = long YES, negative = long NO. Never 0.

**Foreign keys.** Every `REFERENCES` uses the default `ON DELETE NO ACTION`, so a referenced row can never be deleted.
- `event` has a composite foreign key `(series_ticker, underlying_id)` to `series (ticker, underlying_id)`, so an event's underlying must match its series' underlying. `series` has `UNIQUE (ticker, underlying_id)` as the target.
- `event` has `UNIQUE (underlying_id, event_date)`: one event per underlying per day. This catches duplicate events that Kalshi created under a renamed ticker.

**Append-only triggers.** On `candle`, `order_intent`, `order_outcome`, `settlement` and `position_snapshot`, UPDATE, DELETE and TRUNCATE are refused with SQLSTATE `restrict_violation` (function `forbid_change()`).

**Market guard.** On `market`, an open market (`is_settled = false`) may be updated, including the update that sets `result` and `settled_at`, but not its `ticker` or `event_ticker`. A settled market (`is_settled = true`) cannot be updated at all. DELETE and TRUNCATE are always refused. All with `restrict_violation` (function `market_guard()`). `is_settled` is a generated column, `result IS NOT NULL`.

**Run guard.** On `run`, only `status` and `finished_at` may be updated (function `run_guard()`). Any other changed column is refused, DELETE is refused, and TRUNCATE is refused. All with `restrict_violation`.

## Units

- Prices and money are `numeric` dollars (exact decimal), not cents.
- Timestamps are `timestamptz`, UTC. The schema runs `SET timezone = 'UTC'` and the container sets `TZ` and `PGTZ` to UTC.
- Quantities, volumes and units are `numeric`: Kalshi trades fractional contracts (for example a bar volume of 29.29).

## Loading order

Parents before children:

1. `underlying`
2. `series`
3. `event`
4. `market`
5. `candle`
6. `run`
7. `order_intent`
8. `order_outcome`
9. `settlement`
10. `position_snapshot`

A mistake in an append-only table cannot be fixed with UPDATE or DELETE. The only way to wipe the data is to drop the Docker volume, and that should be done only deliberately. The compose file names it
`kalbt_recon_pgdata`; Docker prefixes it with the project (folder) name, so from this worktree it
is `kalbacktester-track-b_kalbt_recon_pgdata` (`docker volume ls` shows it).

## Weather tables

Unit 007 adds five changes at the end of `schema.sql`:

- `station`: new table, one row per weather station.
- `underlying.station_id`: new nullable column linking an underlying to its station.
- `official_daily`, `observation_hourly`, `forecast_hourly`: new tables for the official daily values, the hourly station observations and the hourly model forecasts.

| Table | One row is | Key | Append-only |
|---|---|---|---|
| `station` | A weather station: coordinates, time zone, standard UTC offset, NWS climate report id, IEM id and network | `station_id` (ICAO, e.g. `KNYC`) | no |
| `official_daily` | The official daily high (and low for `nws_cli`) of a station on a local standard time day, from one source (`nws_cli` or `kalshi`) | `(station_id, climate_date, source)` | yes |
| `observation_hourly` | One IEM ASOS report of a station, with the raw METAR | `(station_id, observed_at)` | yes |
| `forecast_hourly` | One model forecast for a station and hour at a lead of 0 to 7 days (Open-Meteo previous runs) | `(station_id, model, valid_time, lead_day)` | yes |

**Units.** Temperatures are degrees Fahrenheit (`_f`), humidity and cloud cover are percent (`_pct`), precipitation is inches (`_in`), radiation is W/m² (`shortwave_wm2`). Wind is knots in `observation_hourly` (`wind_speed_kt`) and miles per hour in `forecast_hourly` (`wind_speed_mph`). Elevation is metres.

**Append-only triggers.** On `official_daily`, `observation_hourly` and `forecast_hourly`, UPDATE, DELETE and TRUNCATE are refused with `restrict_violation` (function `forbid_change()`). `station` is not append-only.

**Seed.** `seed_stations.sql` inserts the KNYC station and links `NYC_HIGH` to it. It is safe to run twice. Apply it after the weather DDL:

```bash
docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < db/seed_stations.sql
```

## Usage

Run these from the repo root. The worktree has no `.env`, so the password (`POSTGRES_PASSWORD`, required by the compose file) comes from the main checkout's `.env`.

Start the container:

```bash
POSTGRES_PORT=5433 POSTGRES_DB=kalbt POSTGRES_USER=kalbt docker compose --env-file ../KalBacktester/.env up -d
```

Connect with psql:

```bash
docker exec -it kalbt-recon-db psql -U kalbt -d kalbt
```

Connection URL (the port is bound to loopback only):

```
postgresql://kalbt:<password>@127.0.0.1:5433/kalbt
```

Run the schema tests on a **throwaway** database built from `db/schema.sql`, never on the live
`kalbt` database: the tests insert their own sample rows (e.g. series `KXHIGHNY`) and expect
empty tables, which clashes with loaded data. They run inside a transaction that is rolled
back. Expect a `PASS` notice per case and no `FAIL` or `ERROR` lines; `truncate cascades to ...`
notices are expected. The throwaway database lives in the same container and is dropped at
the end:

```bash
docker exec kalbt-recon-db sh -c 'createdb -U "$POSTGRES_USER" kalbt_schema_test'
docker exec -i kalbt-recon-db sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < db/schema.sql
docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_schema.sql
docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d kalbt_schema_test' < tests/db/test_weather_schema.sql
docker exec kalbt-recon-db sh -c 'dropdb -U "$POSTGRES_USER" kalbt_schema_test'
```

This also proves that `db/schema.sql` builds cleanly from scratch. Expected: 63 PASS for
`test_schema.sql`, 46 PASS for `test_weather_schema.sql`.

`db/schema.sql` runs only when the data volume is first created (it is mounted as `/docker-entrypoint-initdb.d/01_schema.sql`). Editing it later does not change an existing database.

## How it fits

- Used by: `tests/db/test_schema.sql`. Nothing else uses it yet.
- Uses: nothing. It is the shared contract between tracks (scripts talk through Postgres tables). Changing `schema.sql` needs the lead chat and the user.
