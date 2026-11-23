# 001 — Reconciliation database schema

Track: B (database)

## Goal
An empty PostgreSQL 16 database, created from one `db/schema.sql`, holding the 10 tables the
trade reconciliation needs, with every rule below enforced by the database itself. The user
fills it from JSON files; this unit only creates the empty structure.

## Not built
No loader, no ingestion, no Python code, no migration table, no reconciliation queries, no
weather or forecast tables, no order-book or Kalshi-trade tables. No `bid <= ask` constraint on
candles (crossed quotes can occur in real data; recon reports them instead).

## Files
- `db/schema.sql` (new)
- `docker-compose.yml` (new, repo root of the worktree)

## Interfaces

### Container (`docker-compose.yml`)
- service `db`, image `postgres:16`, container name `kalbt-recon-db`
- env: `POSTGRES_DB` default `kalbt`, `POSTGRES_USER` default `kalbt`,
  `POSTGRES_PASSWORD` required (`${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}`), `TZ: UTC`, `PGTZ: UTC`
- port `127.0.0.1:${POSTGRES_PORT:-5433}:5432` (5433 so it does not clash with the legacy
  `kbt4-db` on 5432)
- volume `kalbt_recon_pgdata:/var/lib/postgresql/data`
- `./db/schema.sql` mounted read-only as `/docker-entrypoint-initdb.d/01_schema.sql`
- healthcheck `pg_isready -U ${POSTGRES_USER:-kalbt} -d ${POSTGRES_DB:-kalbt}`, interval 5s, retries 10

### Types
- prices and money: `numeric` (exact decimal, dollars); timestamps: `timestamptz` (UTC);
  dates: `date`; quantities and units: `integer`; tickers and ids we name: `text`.
- Generated ids: `bigint GENERATED ALWAYS AS IDENTITY`.

### Tables (all columns `NOT NULL` unless marked nullable)

1. `underlying`: `underlying_id text PK`, `description text`.
2. `series`: `ticker text PK`, `underlying_id text FK -> underlying`, `title text` nullable;
   `UNIQUE (ticker, underlying_id)` (needed by the composite FK on `event`).
3. `event`: `ticker text PK`, `series_ticker text`, `underlying_id text`, `event_date date`,
   `settlement_source text`;
   `FOREIGN KEY (series_ticker, underlying_id) REFERENCES series (ticker, underlying_id)`;
   `UNIQUE (underlying_id, event_date)`.
4. `market`: `ticker text PK`, `event_ticker text FK -> event`, `strike_type text` nullable,
   `floor_strike numeric` nullable, `cap_strike numeric` nullable, `open_time timestamptz`,
   `close_time timestamptz`, `result text` nullable, `settled_at timestamptz` nullable;
   CHECK `result IS NULL OR result IN ('yes','no')`;
   CHECK `(result IS NULL) = (settled_at IS NULL)`;
   CHECK `floor_strike IS NULL OR cap_strike IS NULL OR floor_strike <= cap_strike`;
   CHECK `open_time < close_time`.
5. `candle`: `market_ticker text FK -> market`, `ts timestamptz` (bar end, UTC),
   `yes_bid numeric` nullable, `yes_ask numeric` nullable, `volume integer`;
   `PRIMARY KEY (market_ticker, ts)`;
   CHECK `yes_bid IS NULL OR (yes_bid > 0 AND yes_bid < 1)`; same for `yes_ask`;
   CHECK `volume >= 0`. Append-only.
6. `run`: `run_id bigint identity PK`, `strategy text`, `params jsonb` default `'{}'`,
   `code_version text`, `starting_cash numeric`, `date_start date`, `date_end date`,
   `started_at timestamptz` default `now()`, `finished_at timestamptz` nullable,
   `status text` default `'running'`;
   CHECK `status IN ('running','done','failed','discarded')`;
   CHECK `starting_cash > 0`; CHECK `date_start <= date_end`;
   CHECK `(status = 'running') = (finished_at IS NULL)`.
   Only `status` and `finished_at` may be updated; no DELETE, no TRUNCATE.
7. `order_intent`: `intent_id bigint identity PK`, `run_id bigint FK -> run`,
   `decided_at timestamptz`, `market_ticker text FK -> market`, `side text`, `action text`,
   `quantity integer`, `reason text` nullable;
   CHECK `side IN ('yes','no')`; CHECK `action IN ('buy','sell')`; CHECK `quantity > 0`.
   Append-only.
8. `order_outcome`: `outcome_id bigint identity PK`, `intent_id bigint FK -> order_intent`,
   `occurred_at timestamptz`, `outcome text`, `quantity integer`, `price numeric` nullable,
   `fee numeric` nullable, `reason_code text` nullable;
   CHECK `outcome IN ('filled','rejected','blocked')`;
   CHECK named `outcome_fields`:
   `(outcome = 'filled' AND quantity > 0
                        AND price IS NOT NULL AND price > 0 AND price < 1
                        AND fee IS NOT NULL AND fee >= 0)
    OR (outcome IN ('rejected','blocked') AND quantity = 0 AND reason_code IS NOT NULL
        AND price IS NULL AND fee IS NULL)`.
   Append-only.
9. `settlement`: `run_id bigint FK -> run`, `market_ticker text FK -> market`,
   `settled_at timestamptz`, `result text`, `net_units integer`, `payout numeric`;
   `PRIMARY KEY (run_id, market_ticker)`;
   CHECK `result IN ('yes','no')`; CHECK `net_units <> 0`; CHECK `payout >= 0`. Append-only.
10. `position_snapshot`: `run_id bigint FK -> run`, `as_of_date date`,
    `market_ticker text FK -> market`, `net_units integer`, `avg_cost numeric`;
    `PRIMARY KEY (run_id, as_of_date, market_ticker)`;
    CHECK `net_units <> 0`; CHECK `avg_cost > 0 AND avg_cost < 1`. Append-only.

Outcome meaning (agreed with the user 2026-11-23): `blocked` = our own rules stopped the
order before the market (trading gate locked, untrusted settlement day, risk or size limit);
`rejected` = it reached the market but could not execute (no quote, volume cap, not enough
cash, nothing to sell). Add this as the `COMMENT ON COLUMN order_outcome.outcome`.

`net_units` convention everywhere: signed, in YES units (positive = long YES, negative = long NO).
Foreign keys use the default `ON DELETE NO ACTION`, so a referenced row can never be deleted.
Every table and column gets a one-line `COMMENT ON` saying what it holds.

## Hard parts (use as written)

Append-only guard, one function for all append-only tables. The custom error code
`restrict_violation` lets the tests tell a refused change apart from a test failure.

```sql
CREATE FUNCTION forbid_change() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% on % is not allowed: append-only table', TG_OP, TG_TABLE_NAME
        USING ERRCODE = 'restrict_violation';
END;
$$;

-- repeat for: candle, order_intent, order_outcome, settlement, position_snapshot
CREATE TRIGGER candle_no_update_delete
    BEFORE UPDATE OR DELETE ON candle
    FOR EACH ROW EXECUTE FUNCTION forbid_change();
CREATE TRIGGER candle_no_truncate
    BEFORE TRUNCATE ON candle
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
```

`run` guard: only `status` and `finished_at` may change; DELETE refused; TRUNCATE refused with
`forbid_change()` as above.

```sql
CREATE FUNCTION run_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_OP = 'DELETE' THEN
        RAISE EXCEPTION 'DELETE on run is not allowed'
            USING ERRCODE = 'restrict_violation';
    END IF;
    -- compare the whole row except the two columns that may change
    IF (to_jsonb(NEW) - 'status' - 'finished_at')
       IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'finished_at') THEN
        RAISE EXCEPTION 'only status and finished_at may be updated on run'
            USING ERRCODE = 'restrict_violation';
    END IF;
    RETURN NEW;
END;
$$;

CREATE TRIGGER run_guard
    BEFORE UPDATE OR DELETE ON run
    FOR EACH ROW EXECUTE FUNCTION run_guard();
CREATE TRIGGER run_no_truncate
    BEFORE TRUNCATE ON run
    FOR EACH STATEMENT EXECUTE FUNCTION forbid_change();
```

Order in `schema.sql`: `SET timezone = 'UTC';`, the two functions, tables in the order listed
(parents before children), then triggers, then comments.

## Agreed tests
(Approved by the user 2026-11-23. Written by the qa-tester as one SQL file that runs inside
`BEGIN; ... ROLLBACK;` so the database is left empty.)

1. `schema.sql` applies to an empty database with no errors; exactly the 10 tables exist.
2. A valid chain inserts: underlying → series → event → market → candle; run → order_intent →
   order_outcome (one filled, one rejected, one blocked) → settlement → position_snapshot.
3. Each CHECK rejects one bad row (SQLSTATE `check_violation`): market result `'maybe'`;
   market result without `settled_at`; floor above cap; candle bid `0`, ask `1`, volume `-1`;
   run status `'paused'`, `starting_cash 0`, `date_start > date_end`, status `done` without
   `finished_at`; intent side `'up'`, action `'hold'`, quantity `0`; filled outcome without
   price; rejected outcome with quantity `5`; blocked outcome without `reason_code`;
   settlement result `'maybe'`, `net_units 0`, negative payout; snapshot `net_units 0`,
   `avg_cost 1`.
4. A foreign key rejects orphans (`foreign_key_violation`): market with unknown event; intent
   with unknown market; outcome with unknown intent.
5. `UNIQUE (underlying_id, event_date)` rejects a second event for the same underlying and day
   (`unique_violation`).
6. The composite foreign key rejects an event whose `underlying_id` differs from its series'
   (`foreign_key_violation`).
7. On each of `candle`, `order_intent`, `order_outcome`, `settlement`, `position_snapshot`:
   UPDATE, DELETE and TRUNCATE are each refused (`restrict_violation`).
8. `run`: updating `status` to `done` with `finished_at` set succeeds; updating `strategy` is
   refused; DELETE refused; TRUNCATE refused (`restrict_violation`).
9. Deleting a `market` referenced by an `order_intent` is refused (`foreign_key_violation`).
10. After the test file finishes, every table is empty (the transaction rolled back).

Pattern for "expect this error" (a wrong or missing error escapes and stops the file):

```sql
DO $$ BEGIN
    BEGIN
        INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)
        VALUES ('M1', '2026-06-01 12:00+00', 0, 0.5, 10);
        RAISE EXCEPTION 'FAIL: candle bid 0 was accepted';
    EXCEPTION WHEN check_violation THEN
        RAISE NOTICE 'PASS: candle bid 0 rejected';
    END;
END $$;
```

## Test files
- `tests/db/test_schema.sql` (new)

## Legacy reference
`legacy/sql/schema.sql`, `legacy/sql/results.sql`, `legacy/docker-compose.yml`: style of CHECK
constraints and the compose file. The tables themselves are redesigned, not copied.

## Done when
Run by the lead (the worktree has no `.env`; it is passed from the main checkout):
1. `docker compose --env-file ../KalBacktester/.env up -d` and the container reports healthy.
2. `docker exec -i kalbt-recon-db sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' < tests/db/test_schema.sql`
   exits 0, prints a `PASS` notice per case and no `FAIL` or `ERROR` lines. (`truncate
   cascades to ...` notices are expected: the TRUNCATE tests need `CASCADE` on referenced tables.)

Accepted at review: `restart: unless-stopped` and healthcheck `timeout: 5s` in the compose file.

If anything here is unclear or looks wrong: stop and report, do not guess.
