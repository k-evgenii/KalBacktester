# Handoff to Track B: the ingestion QA gate

From: lead chat, Track A (branch `dev`), 2026-11-23.
To: the Track B chat (worktree `/home/kondr/KalBacktester-track-b`, branch `track-b`).

Read this whole file, then `.claude/CLAUDE.md`, before doing anything. Nothing here is
approved yet: the check list below is a proposal. Your first job is to agree it with the user,
write it up as a spec, and only then run the build loop.

---

## 1. What you are being asked to build

A **QA gate** that sits between a raw JSON dump and the database. It reads a dump folder,
runs every check below, writes a report, and says either **PASS** (the loader may insert) or
**BLOCK** (nothing is inserted). The loader must refuse to run on a BLOCK.

Why it is needed now: the current loader inserts with `ON CONFLICT DO NOTHING`. That keeps it
safe to rerun, but it also means a refetched market whose data **changed** is silently ignored,
and `candle` is append-only, so a bad row can never be corrected afterwards. Checks must
therefore happen **before** the insert, not after.

Scope: Kalshi market data first (the one-week `KXHIGHNY` dump that already exists). Weather
data (forecasts, official daily high) will follow the same pattern later; do not build for it
yet.

---

## 2. Get in sync first

Track A's work is on `dev` and **not yet in `track-b`**. Your worktree is clean at `3d8e376`,
so it is missing the schema change, the loader, the dump and the notebook. Before starting:

1. Ask the user to confirm Track A's work has been committed on `dev` (the lead chat commits it
   on the user's word).
2. In your worktree: `git merge dev` (fast-forward expected).
3. The dump itself is ignored by git and exists only in the main checkout:
   `/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY/`. Read it from that absolute path; do
   not copy it into the repo.

---

## 3. What already exists (after the merge)

| Path | What it is |
|---|---|
| `db/schema.sql` | your 10-table schema, with one change from Track A (below) |
| `db/connect.py` | `connect()` → psycopg 3 connection to `kalbt-recon-db` (127.0.0.1:5433, db/user `kalbt`, password from env or `.env`) |
| `ingest/load_kxhighny_dump.py` | the loader: dump → `underlying`, `series`, `event`, `market`, `candle`; one transaction; `ON CONFLICT DO NOTHING` |
| `api_probe/kalshi/dump_kxhighny.py` | fetches a date range of `KXHIGHNY` from the public API into the dump folder |
| `api_probe/kalshi/check_dump.py` | unit 002's tests on the dump (good reference for check code) |
| `tests/ingest/check_load_kxhighny_dump.py` | unit 003's tests: database vs dump, row by row (reference) |
| `api_probe/kalshi/KXHIGHNY_FIELDS.md` | every field of markets, bars and trades, live vs historical names |
| `api_probe/README.md` | API cookbook: commands, pitfalls, coverage tables |
| `debugging/kalshi_market_data/plot_kxhighny_week.ipynb` | plots of the loaded week |
| `.claude/specs/002-…`, `003-…` | the specs for the dump and the loader |

**Schema change Track A made (approved by the user):** five columns went from `integer` to
`numeric`, because Kalshi trades fractional contracts (8,321 of 41,158 bars have a non-whole
volume, e.g. 29.29): `candle.volume`, `order_intent.quantity`, `order_outcome.quantity`,
`settlement.net_units`, `position_snapshot.net_units`. `db/README.md` and spec 001 were updated
to match, the live database was altered, and your 55 schema tests still pass.

**Database state:** `kalbt-recon-db` holds the loaded week: 1 underlying (`NYC_HIGH`),
1 series, 7 events, 42 markets, 41,158 candles. Trading tables are empty. Do not wipe it.

---

## 4. Facts about the data the checks must respect

All verified on the loaded week (16–22 Nov 2026) unless marked otherwise.

**Dump layout** (`api_probe/kalshi/KXHIGHNY/`):
- `markets.json` = `{"start", "end", "markets": [market objects as returned by the API]}`
- `bars_1min/<ticker>.json` = `{"ticker", "surface": "live"|"historical", "candlesticks": [...]}`
- `trades/<ticker>.json` = `{"ticker", "surface", "trades": [...]}` (trades are not loaded; no table)
- `_report.json` = the fetcher's own descriptive report; **do not trust it as input to checks**

**Prices and quantities**
- Prices arrive as dollar strings (`"0.0100"` = 1 cent), never cents. Parse with `Decimal`, never float.
- Live bars use `close_dollars` / `volume_fp`; historical bars use `close` / `volume`. Both must be handled.
- A closing bid of `0.00` means **no bid**; a closing ask of `1.00` means **no ask**. The loader
  stores these as NULL. This is a translation, not a defect (8,107 and 620 bars in the week).
- Volumes and trade sizes are fractional.
- A bar's `price` object is `{}` or has only `previous_dollars` when nothing traded that minute.

**Bars**
- Bars are **sparse**: a bar exists only for minutes with activity (13 % to 70 % of open
  minutes per market, median 48 %). A missing minute is not missing data.
- Forward-fill rule for anything downstream: carry the **whole previous bar** across a missing
  minute; a bar that is present with a NULL bid means there is no bid at that time. Never fill
  the bid and ask columns separately (the notebook did, and it produced false alarms).
- No bar in the week has bid ≥ ask.

**Markets and events**
- 6 markets per event: 1 `less` (only `cap_strike`), 1 `greater` (only `floor_strike`),
  4 `between` (both). The unused strike field is **absent**, not null.
- The strikes tile the temperature range with no gap or overlap, in whole degrees, e.g.
  `<61  61-62  63-64  65-66  67-68  >68`: `less.cap == first between.floor`;
  each `between.cap + 1 == next between.floor`; `greater.floor == last between.cap`.
- Exactly one market per event resolves `yes`.
- Settlement rule (verified on all 42): `greater` is yes iff value > floor; `less` is yes iff
  value < cap; `between` is yes iff floor ≤ value ≤ cap. Ties resolve **no**.
- Times, identical for all 42 markets: `open_time` = event day − 9 h (15:00 UTC the day
  before), `close_time` = event day + 30 h (06:00 UTC the day after). Settled 6 h 7–12 min after close.
- `status` is `finalized` once settled; `result` is `""` while open.
- Seen once in older data (not this week): a settled market with an empty `expiration_value`
  (`KXHIGHNY-26JUN23-T78`).
- The event date comes from the ticker (`KXHIGHNY-26NOV22` → 2026-11-22).
- Settlement source in the rules: The Weather Company. It matched the NWS CLI daily high on
  490 of 491 New York days checked (one day off by 1 °F, 2025-06-18).

**Bars vs trades vs quotes**
- Per market, summed bar volume equals summed trade size (within 0.5 contracts).
- No trade after `close_time`.
- When all six markets have a bar in the same minute, the sum of asks is about $1.06–1.07 and
  the sum of bids about $0.97–0.98: the $1 payout sits between them (no free arbitrage).

---

## 5. Proposed checks (for you and the user to agree)

Two severities only, to keep it simple:
- **BLOCK** — the dump is not inserted at all. The loader is all-or-nothing already.
- **WARN** — recorded in the report, the load goes ahead.

### A. File and envelope (BLOCK)
1. `markets.json`, `bars_1min/` and `trades/` exist and parse as JSON.
2. `start` ≤ `end`; the events in `markets.json` are exactly the event tickers generated from
   `start..end` (none missing, none extra).
3. Bars and trades files match the markets one to one; each file's `ticker` equals its filename;
   `surface` is `live` or `historical` and the same in a market's bars and trades files.

### B. Record parsing (BLOCK)
4. Every required market field is present and parses: `ticker`, `event_ticker`, `strike_type`,
   `open_time`, `close_time` (RFC 3339), strikes as numbers where the strike type needs them.
5. Every bar parses: `end_period_ts` is an integer epoch; bid, ask and volume parse as
   `Decimal` under either key style; prices within [0, 1]; volume ≥ 0.
6. No unknown key style: a bar that has neither the live nor the historical names is a failure,
   not a NULL.

### C. Domain rules per market (BLOCK)
7. Strike shape matches `strike_type` (less: cap only; greater: floor only; between: both,
   floor < cap).
8. `open_time < close_time`.
9. A settled market (`status == finalized`) has `result` in yes/no and a `settlement_ts`.
10. Settlement consistency (rule in section 4) wherever `expiration_value` is present.
11. Within a market's bars: `end_period_ts` strictly increasing, no duplicates, every bar inside
    `open_time..close_time`, and no bar with bid ≥ ask (after the 0/1 translation).

### D. Consistency across an event (BLOCK unless noted)
12. Exactly one `less` and one `greater` market per event, and the strikes tile with no gap or
    overlap.
13. For a settled event, exactly one market resolved `yes`.
14. *(WARN)* Market count per event differs from 6; open/close offsets differ from −9 h / +30 h.
15. *(WARN)* Ask-sum below $1 or bid-sum above $1 in any minute where all markets have a bar
    (a possible arbitrage, or a bad quote).

### E. Against what is already in the database (BLOCK)
16. **Same key, different values.** For every market and candle that already exists in the
    database, the incoming values must equal the stored ones. Any difference blocks the load and
    is listed. (This is the case `ON CONFLICT DO NOTHING` hides today.)
17. The `underlying` and `series` the dump belongs to exist, or are part of this load.

### F. Coverage and quality (WARN)
18. Markets with no bars, no trades, or a share of minutes with a bar below a threshold to agree
    (e.g. 5 %).
19. Bar volume vs trade volume differs by more than 0.5 contracts in a market.
20. Trades after `close_time`; settled markets with an empty `expiration_value`.
21. Counts of translations applied (bids 0 → NULL, asks 1 → NULL), so a sudden jump is visible.

---

## 6. Proposed shape (keep it minimal; CLAUDE.md section 1 applies)

- One module, e.g. `ingest/qa_kxhighny_dump.py`, with plain functions:
  `run_checks(dump_dir, conn) -> report` where the report is a dict
  `{"verdict": "PASS"|"BLOCK", "checks": [{"id", "severity", "passed", "detail", "examples"}], "counts": {...}}`.
- Runnable on its own (`uv run python -m ingest.qa_kxhighny_dump`): prints one line per check,
  writes `_qa_report.json` next to the dump, exits 0 on PASS and 1 on BLOCK.
- The loader calls `run_checks` first and stops on BLOCK. That gives the function two real
  callers, which is why it is a function and not inline code.
- Checks read only the dump files and the database; never `_report.json`.
- Read-only on the database: SELECT only.

**Open decision for the user:** should each QA run be recorded in the database, e.g. an
append-only `load_batch` table (batch id, dump folder, file hash, run time, verdict, counts,
report as jsonb), with candles pointing to the batch that inserted them? It gives an audit trail
for reconciliation, but it is a schema addition, so it is your and the user's call. Without it,
the `_qa_report.json` file is the record.

---

## 7. Proposed tests (for the user to approve; the qa-tester codes them)

Each test copies the real dump to a temporary folder, makes **one** deliberate change, runs the
gate, and expects the stated verdict and failing check id. The real dump must be left untouched.

1. The untouched dump → PASS, with the WARN items reported.
2. Delete one event's markets from `markets.json` → BLOCK, check 2.
3. Delete one bars file → BLOCK, check 3.
4. Corrupt one bar's ask to `"abc"` → BLOCK, check 5.
5. Rename `close_dollars` to `closing` in one bar → BLOCK, check 6.
6. Flip one market's `result` from no to yes → BLOCK, checks 10 and 13.
7. Swap two bars so time goes backwards → BLOCK, check 11.
8. Change one `between` market's floor so the ladder has a gap → BLOCK, check 12.
9. Change one bar's volume in a market that is already in the database → BLOCK, check 16,
   naming the market and timestamp.
10. Running the gate leaves every table's row count unchanged (read-only).
11. The loader, given a dump that BLOCKs, inserts nothing and exits non-zero.

---

## 8. Process reminders

- Branch: work on `track-b`, never on `main`. Only the lead chat merges into `dev`.
- Plan with the user first; the spec goes in `.claude/specs/004-ingestion-qa.md` (004 is free;
  001 is yours, 002 and 003 are Track A's).
- Then the loop: `implementer` → `qa-tester` → `reviewer` (Sonnet, medium effort), findings back
  to the implementer, escalate after 3 rounds or a repeated failure.
- Workers never print the database password and never UPDATE, DELETE or TRUNCATE anything.
- Out of scope for you: fetching from the API (Track A), weather data, the backtest engine.

## 9. Track A's open items you may hear about

- The notebook's sum-of-prices cell and last-quote table fill bid and ask separately and show
  false alarms; a two-cell fix is pending with Track A.
- The fetcher's `n_gaps` figure counts quiet minutes, not missing data; to be renamed.
- Next in Track A: the weather side (forecasts and the official daily high for the same week).
