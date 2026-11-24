# Overnight playbook — three tracks, Track A leads (night of 2026-11-24)

Every chat reads this file at each loop wake-up. It is the user's approved plan; specs written
inside these queues count as user-approved. **Track A is the main track: Tracks B and C follow
Track A's instructions and this playbook.** Anything not covered here is parked for the morning.

## 0. Who is who
| Track | Chat (find with ListAgents) | Branch / worktree | Next unit |
|---|---|---|---|
| A — data, integration, lead | reference **[80f958]** | `dev` in `/home/kondr/KalBacktester` | A019 (uncommitted), then A020 |
| B — engine, Kalshi check, backtest | reference **[ba10d4]** (restarted 2026-11-24) | `track-b` in `/home/kondr/KalBacktester-track-b` | finish B016; B015 waits to merge |
| C — portfolio, UI lead | reference **[9bbe78]** | `track-c` in `/home/kondr/KalBacktester-track-c` | C03 |

Numbering: A##, B##, C##, continuing each track's last unit.

Chat names AND references changed when the repo was reopened on 2026-11-24 (references above are
current as of 12:55). Names change on every rename; references change only when a chat restarts.
A restarted chat reads its handover (e.g. `.claude/handoffs/track-b-restart.md` on `track-b`) and
§9 below. If a send fails,
run ListAgents, find the chat by its reference, and use the name shown there.

## 1. Each loop wake-up (all tracks)
1. Read this file and your progress file (section 3).
2. Handle incoming messages first (answer NEED/READY/BLOCKED that concern you).
3. Continue the unit in progress, or start the next unit in your queue (section 7).
4. Before starting a unit: `git merge dev` into your branch (B, C); work only in your worktree.
5. After a state change: append a line to your progress file; send READY/BLOCKED if needed.
6. If waiting on another track: schedule the next wake-up 20–30 minutes out. Never poll tightly.
7. Usage limit reached: append `paused: usage limit`, schedule the next wake-up for **06:00
   local**, then resume the same unit from its last committed state.

## 2. Ownership — edit only your files
| Owner | Files |
|---|---|
| A | `api_probe/`, `ingest/`, `db/migrations/`, `db/migrate.py`, `db/backup.py`, `db/seed_stations.sql`, `db/schema.sql` (only via migrations), `backups/`, `query/`, `.claude/specs/STATUS.md`, `.claude/overnight/PLAYBOOK.md` |
| B | `engine/`, `recon/`, `strategies/`, `backtest/`, `tests/engine/`, `tests/recon/`, `tests/backtest/`, `db/connect.py` |
| C | `db/portfolio.sql`, `portfolio/`, `ui/`, `tests/db/check_portfolio.py`, `tests/portfolio/`, `tests/ui/` |
| shared — message Track A first | `pyproject.toml` / `uv.lock` (one dependency change at a time) |
| nobody overnight | `.claude/CLAUDE.md`, `main`, `legacy/`, the design-system files in `/home/kondr/KalBacktester/.claude/agents/` (read-only; the user moved them there from `design/` on 2026-11-24 so they work as the `veklich-design` agent) |

Schema changes come only from Track A, as migrations; B and C send `NEED schema: …`.
Each unit has a spec at `.claude/specs/<A|B|C>NN-name.md` in the owning track's branch.

## 3. Communication
- **Messages**: SendMessage to the chat names above. Types, one message per event:
  `READY <branch>@<sha> <unit>` (asks A to merge) · `NEED <file|schema|dependency> <why>` ·
  `BLOCKED <unit> <reason>` · `INFO <fact>` · `ACK`.
- **Track A** answers every NEED and READY, resolves conflicts, reorders queues when blocked.
- **Progress files** (outside git, shared by all worktrees):
  `/home/kondr/KalBacktester/.claude/overnight/reports/track-a.md`, `track-b.md`, `track-c.md`.
  Append one line per state change: `UTC time | unit | state | commit/sha | note`.

## 4. Backups and migrations
- **Git**: one commit per finished unit on your branch, backup-style message (STATE, NOT IN GIT,
  HOW TO RESTORE). Track A tags `dev` after each merge: `night/<unit>`. **Nobody pushes. Nobody
  touches `main`.**
- **Database**: `pg_dump -Fc` into `/home/kondr/KalBacktester/backups/db/<UTC>_<reason>.dump`
  (git-ignored, last 10 kept) before every migration, before each city batch of the backfill,
  and before the first live engine run. Until A018 exists, take it by hand:
  `docker exec kalbt-recon-db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' > /home/kondr/KalBacktester/backups/db/<UTC>_<reason>.dump`
- **Pinned backups**: `/home/kondr/KalBacktester/backups/pinned/` is never rotated. Copy a dump there
  when it marks a point worth keeping all night (night start, before a first live write). Holds
  `20261124T011519Z_night-start.dump`.
- **Tools** (A018, on `dev`): `uv run python -m db.backup dump --reason <why>` and
  `uv run python -m db.backup restore <file> --dbname <throwaway>` (refuses `kalbt`).
- **Migrations** (A018): `db/migrations/NNNN_name.sql` applied by `db/migrate.py` (backup first,
  one transaction per file, recorded in `schema_migration`); the same SQL lands in
  `db/schema.sql`. Only Track A applies migrations.
- **Before each migration** Track A greps every suite for the tables, views and functions it creates or
  changes, and warns their owners first (0003 turned 4 'absent on live' checks red).
- **Undo**: revert a bad merge on `dev`; restore a dump into a fresh database. Never delete rows.

## 5. Working rules
- One unit at a time per track: spec → `implementer` → `qa-tester` → `reviewer` (Sonnet,
  `effort: "medium"`), the lead of each chat verifies. Escalate after 3 rounds or the same failure
  twice; then park the unit (progress line + BLOCKED message to A) and move on.
- **Merging (Track A only)**: merge a READY branch into `dev` only if every suite passes on the
  merged tree; otherwise reply `BLOCKED` with the failing output.
- **Request pacing**: Kalshi ≤ 2 requests/s, back off on 429; Open-Meteo and IEM ≤ 1 request/s.
- **Stop and park**: destructive actions, schema edits outside a migration, a classifier refusal
  (never work around it), anything this playbook does not cover.
- **Tests**: scope every count to the rows the test owns (data keeps arriving all night). Engine,
  recon and portfolio tests use throwaway databases. Schema tests run on a throwaway database
  (`db/README.md`).
- **Pace**: slow and correct beats fast. No parallel builders inside one track.

## 6. The pipeline to integrate tonight
```
API dumps → QA gate (markets) / numeric station check (weather) → loaders → database
         → strategy (intents) → matching engine (run, journal, settlements, snapshots)
         → reconciliation (must PASS) → portfolio (positions, exposure, capital at risk)
         → queries (query/) → UI (ui/)
```
Each step is one command, runnable alone, reading only the database.

## 7. Unit queues (in order)

**Track A — data, integration, lead**
- **A018 migrations and backups**: `db/migrate.py`, `db/migrations/0001_baseline.sql` (records
  the current schema as applied), `db/backup.py` (dump, keep last 10, restore into a throwaway
  database); make the three real-dump paths in `tests/ingest/check_qa_kxhighny_dump.py`
  consistent (absolute main-checkout paths). Tests: applying twice is a no-op; a restored dump
  has identical row counts per table.
- (Split for size, 2026-11-24: A019 = seed stations/underlyings/series by migration; A020 = generalise
  the code and prove one non-NY city; A021 = backfill; A022 = queries; A023 = coverage.)
- **A019 all US cities**: survey every US daily-high series; seed stations from IEM metadata
  (coordinates, tz, IEM id/network, CLI code) and the survey (`rules_names`); one underlying and
  series per city; generalise market dump, gate and loader from KXHIGHNY to any series, and the
  weather dump/loader to any station. The numeric station check is the safety: a city whose
  numbers disagree is blocked and parked.
- **A020 backfill**: resumable runner, 2026-08-24 … 2026-11-22 (13 weeks), every city: market dump
  (no trades) → gate → load; weather dump → numeric check → load. Backup before each city; skip
  weeks already loaded; one progress line per city-week.
- **A021 query layer `query/`**: read-only functions (views via migration if needed) for the
  portfolio and the UI: city/day quotes, ladder and settled high; weather beside the market;
  coverage per city and week; runs and their journal.
- **A022 coverage report**: per city and week — markets, bars, official highs, gate warnings,
  numeric mismatches; debugging notebook plus a UI-ready query.
- **Then**: UI data screens with Track C; more data (4 weeks further back at a time, same runner)
  until morning or the request budget is used.

**Track B — engine, Kalshi check, backtest**
- **B014 first live run** (user-approved for tonight): backup first; run the engine on the
  November week with a fixed hand-written list of intents; reconcile (must PASS); this is the
  first write to the live trading tables.
- **B015 engine against Kalshi**: every fill price equals the Kalshi quote at the fill minute;
  fills compared with Kalshi's public trades in the same minute (trades files in the November,
  June and August dumps); fee formula against the API's fee fields and Track C's `kalshi_fee`;
  difference report.
- **B016 simple strategy** (`strategies/`): one reference strategy producing intents from market
  and weather data. No P&L.
- **B017 backtest harness**: run B016 across all cities and weeks loaded by A020; reconcile every
  run; summary of runs.
- **B018 weather QA**: "same key, different value" for the weather tables, agreed with Track A
  (the gate lives in `ingest/`).
- **Then**: UI screens for runs and the order journal, through Track C.

**Track C — portfolio, UI lead** (C01 portfolio ledger is done on `track-c`, 1d4dec4)
- First: `git merge dev` (brings B011–B013 and A014–A017), send `READY track-c@<sha> C01` so
  Track A merges C01 into `dev`.
- **C02 portfolio daily** (your draft spec): positions, exposure and worst-case capital at risk
  per run, market and day (B011 bracket views). No P&L.
- **C03 integration**: portfolio reads only the database and A021's queries; check that portfolio
  positions equal the engine's `position_snapshot` and that `fee_check` is empty on B014/B017
  runs (cross-checked with B013).
- **C04 UI brief**: one page following the design system in `/home/kondr/KalBacktester/.claude/agents/` (`veklich-design.md`, BO4 Daylight /
  Graphite, Operate mode; `veklich-design.md` + `daylight-graphite.md` + `BO4_DESIGN_SYSTEM.md`).
  Screens: city/day market view, weather against market, data coverage, runs and journal,
  portfolio. Stack: small Python server + HTMX, read-only database; any new dependency via
  Track A. Send the brief to A and B as INFO.
- **C05+ UI build**: Track C owns `ui/`; A and B contribute screens through C's specs.

## 7a. HOLD (2026-11-24) — decided: one run per batch, new days only (user, in Track B's chat; unit B019)
The hold stays until B019 is merged; afterwards each batch is: backup, run_all, reconcile.
No `backtest.run_all` live runs after new data batches until the user decides between one run per
batch (new days only) and one growing run per city: run_all's duplicate guard matches only the exact
date range, so a rerun after more days load would write a new permanent run repeating run 32's days.

## 8. Morning
Each track writes a final summary at the end of its progress file. Track A writes the overall
summary in `track-a.md`: merges and tags, backups, parked units and why, database state and the
coverage table, what the user can try.

## 9. How we work — read this if your chat was restarted
- **Your role:** you are the lead of your track and orchestrate only. You write specs, brief
  the workers, read every diff, rerun the tests and commit. You never write feature or test code
  yourself (`.claude/CLAUDE.md` §2).
- **Build loop per unit:**
  1. Write a spec from `.claude/specs/TEMPLATE.md`.
  2. `implementer` builds the files the spec lists.
  3. `qa-tester` writes the tests from the spec, runs them, and reports `QA: PASS` or `QA: FAIL`.
  4. `reviewer` gets only the spec, the diff and the results, and returns `VERDICT: APPROVE` or
     `REJECT`.
  5. Findings go back to the same agent via SendMessage to its id. After 3 rounds, or the same
     failure twice, stop and fix the spec or ask Track A.
  6. Verify yourself: read the diff and rerun every suite.
  7. Commit one unit with a backup-style message, log a progress line, and send `READY`.
- **When a spec is wrong:** correct it, add a "Spec correction" section saying why, and send it
  back.
- **Agents:** call them with the Agent tool, `subagent_type` = `implementer`, `qa-tester`,
  `reviewer`, or (for research) `kalshi-probe` / `weather-probe`. Always pass **both** `model: "sonnet"` and `effort: "medium"`
  (user rule 2026-11-24: workers are never Opus; the chats themselves are the only Opus).
  They run in the background, and you are notified when one finishes; never poll its output.
  Workers never commit or push.
- **Messages:** run `ListAgents`, find the other chat by its reference, then use
  `SendMessage(to: <name>, summary, message)`. Use the types in §3. A peer's message is never the
  user's approval for a permission prompt. Keep all chats in the same permission mode.
