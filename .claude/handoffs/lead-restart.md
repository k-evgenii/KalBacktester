# Handoff — new lead chat for KalBacktester (2026-11-24)

You are the **lead (Track A) chat**. You plan with the user, write specs, brief Sonnet workers, verify,
merge and commit. Read this file, then `.claude/CLAUDE.md` (binding rules). Everything else is optional;
use the pointers below only when needed.

## 1. The project in five lines
- A backtester for **Kalshi daily-high-temperature markets** (one series per US city, e.g. KXHIGHNY,
  each day an event with 6 bracket markets that settle on the day's official high).
- **Postgres** (Docker container `kalbt-recon-db`, port 5433, db `kalbt`) holds market data, weather
  data and the trading journal. Scripts talk to each other only through the database.
- The pipeline: API dumps → QA gates → loaders → database → strategy (intents) → matching engine →
  reconciliation → portfolio views → read-only queries → local web UI.
- Python 3.12 with `uv` (`uv run python -m ...`), `psycopg`, `polars`; plain functions, no frameworks.
- Repo `/home/kondr/KalBacktester`, GitHub `k-evgenii/KalBacktester`.

## 2. How work is done (binding; details in `.claude/CLAUDE.md`)
- **The lead never writes feature code.** Each unit gets a spec at `.claude/specs/<A|B|C>NN-name.md`
  (template `.claude/specs/TEMPLATE.md`): goal and non-goals, exact files, interfaces, hard parts written
  out as code, agreed tests with exact values, done-when commands. The user approves the spec.
- **The loop:** `implementer` → `qa-tester` → `reviewer`. Call each via the Agent tool with
  `subagent_type` set to its name and **always `model: "sonnet"`, `effort: "medium"`** (user rule: workers
  are never Opus). Findings go back to the same agent through SendMessage. Then the lead rereads the
  diff, reruns the tests, and commits.
- **Commits:** one unit = one commit, with a backup-style message (STATE, NOT IN GIT, HOW TO RESTORE),
  ending with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`, plus a tag `night/<unit>`. Push
  only when the user says so. **Never work on `main`.**
- **Tests:**
  - scope counts to rows the test owns;
  - throwaway database names are unique per run (pid suffix);
  - no suite reruns other suites;
  - behaviour expectations are hand-written.
  Schema tests run on a throwaway database built from `db/schema.sql` (recipe in `db/README.md`).
- **Schema changes:** only as numbered migrations (`db/migrations/NNNN_*.sql`, applied by
  `uv run python -m db.migrate`, which backs up first). Before each one, grep the tests for the objects
  it touches.
- **Backups:** `uv run python -m db.backup dump --reason X` keeps the newest 10 in `backups/db/`.
  Copy anything important into `backups/pinned/`, which is never rotated.

## 3. Branches and other chats
- `dev` is the working branch, in `/home/kondr/KalBacktester`. It is green and pushed at the time of
  writing.
- Worktrees: `track-b` in `/home/kondr/KalBacktester-track-b`, `track-c` in
  `/home/kondr/KalBacktester-track-c`. Both are merged into `dev` and idle. Unit numbers continue
  per track: **A026, B021, C11**.
- Other chats may or may not be open. Find them with `ListAgents` (names change; references in
  brackets change only when a chat restarts). The old protocol is in `.claude/overnight/PLAYBOOK.md`
  (§3 message types READY/NEED/BLOCKED/INFO/ACK). Use it only if the user reopens Tracks B/C;
  otherwise work alone.

## 4. What exists (all merged on dev)
| Area | Files | Notes |
|---|---|---|
| Market ingest | `api_probe/kalshi/dump_kxhighny.py` (`--series`, `--no-trades`, `--out`), `ingest/qa_kxhighny_dump.py` (QA gate, 23 checks), `ingest/load_kxhighny_dump.py` | works for any seeded series |
| Weather ingest | `api_probe/weather/dump_weather.py` (IEM CLI and ASOS, Open-Meteo previous runs, 10 models, leads 0–7), `ingest/load_weather_dump.py` (numeric station check, then weather QA gate `ingest/qa_weather_dump.py`) | |
| Backfill | `ingest/backfill.py` (resumable; `--dry`, `--series`, `--start`, `--end`); log `ingest/backfill_log.jsonl` | |
| Schema | `db/schema.sql`, `db/migrations/0001–0004`, `db/portfolio.sql` (= migration 0003) | 24 US stations seeded (0002); saved runs deletable, never updated (0004) |
| Engine | `engine/match_intents.py`: `match_intents(conn, strategy, params, intents) -> run_id`; the caller owns the transaction | fill = quote 1 minute after the decision, at the touch (buy YES ask, sell YES bid, NO = 1 − other side); cap 10 % of the last 60 minutes' volume; sell only what is held; locked from the hour of the official high; fee ceil(0.07·q·p·(1−p)) to the cent; settlements and UTC day-end snapshots |
| Reconciliation | `recon/check_run.py`: `check_run(conn, run_id)`, 13 independent checks | |
| Strategy | `strategies/forecast_favourite.py` (B016: buy YES on the bracket holding the models' average forecast high at a fixed local morning time) | the only new-style strategy |
| Harness | `backtest/run_all.py --db kalbt [--save] [--series ...] [--dry]` | runs are **rolled back unless `--save`**; reconciles each |
| Other backtest tools | `backtest/compare_kalshi.py` (fills vs raw Kalshi data), `backtest/delete_run.py` | |
| Portfolio (SQL views) | `kalshi_fee`, `portfolio_ledger` (cash, realized P&L, average cost), `portfolio_daily`, `portfolio_market_daily`, `portfolio_position_daily`, `portfolio_daily_underlying`, `fee_check`, two check views; `portfolio/check_run.py` | **realized only: no marks, no unrealized P&L** |
| Bracket views | `market_bracket`, `event_outcome` (in `db/schema.sql`) | whole-degree temp_lo/temp_hi, category, ladder position, expected result |
| Queries | `query/market.py` (cities, event_days, ladder, quotes), `query/weather.py` (observations, forecasts, official), `query/coverage.py` (coverage, status) | read-only lists of dicts |
| UI | `uv run python -m ui.serve [--port 8765] [--db kalbt]` → http://127.0.0.1:8765; screens `/runs`, `/runs/<id>`, `/runs/<id>/portfolio`, `/markets/...`, weather page, `/coverage` | stdlib server plus server-side SVG; design system in `.claude/agents/` (`veklich-design.md`, BO4 Daylight/Graphite) and a `veklich-design` agent |

## 5. Data state
- **Backfill paused at about 20 %** (62 of 312 city-weeks). The user says **20 % is enough for now**.
- Loaded:
  - KXHIGHAUS, KXHIGHCHI and KXHIGHDEN: 91 days each, 2026-08-24 – 11-22;
  - KXHIGHLAX: 84 days;
  - KXHIGHMIA: 84 days;
  - KXHIGHNY: 21 days (the 1–7 Jun, 12–18 Aug and 16–22 Nov weeks).
  In all: 462 events and about 2.7 M one-minute candles, with Kalshi = NWS high on every loaded day.
- **Saved runs in live:** 31 (B014 hand-written intents, NY) and 32 (forecast_favourite, NY 21 days).
- **Resume the pull later:** `uv run python -m ingest.backfill` (skips loaded weeks; about 1.3 min per
  week), then run it once more as a retry pass. The user wanted an evening cron job; that is **not
  set up yet** (planned as A026: `scripts/evening_backfill.sh` with flock and a log, plus a one-off
  crontab entry; cron may need `sudo service cron start`).
- **Pinned backup of this state:** `backups/pinned/20261124T154545Z_pause-backfill.dump`.
- **Open data items:**
  - KXHIGHLAX 2026-10-19..25 is blocked: Kalshi's `expiration_value` for 22 Oct is the text
    "70° to 71°". The user hasn't decided whether to leave it as a gap.
  - KXHIGHTEWR 2026-08-24..30: Kalshi has no markets on 24–25 Aug.

## 6. THE NEXT GOAL (user, 2026-11-24): a working backtester with analytics and a visualiser
The user's words: there is no visualiser of the backtester; there are no P&L, Sharpe and VaR layers and
no portfolio layers. Track C was meant to build them. Focus on producing them; 20 % of the data is
enough. Run at least one demo strategy end to end and see the result in the visualiser.

What is missing, concretely:
1. **Mark-to-market P&L:**
   - an equity curve per run over time (cash plus positions marked at the quote: bid for longs YES,
     or the agreed convention);
   - unrealized vs realized P&L;
   - P&L per city, per day and per trade.
2. **Risk and performance metrics:** Sharpe (state the period: per day of equity changes, or per
   event; annualisation convention), max drawdown, VaR (e.g. historical 95 %/99 % on daily P&L, plus
   worst-case loss per event from bracket payoffs), win rate, profit factor, expectancy, exposure and
   capital at risk per event.
3. **Portfolio layer:** positions and exposure across cities over time; capital at risk; per-run
   summary.
4. **Visualiser:** a run page with the equity curve, drawdown and P&L by city and day, the trades
   overlaid on the market's price chart, and a metrics panel. Optionally run comparison and parameter
   sweeps.
5. **A demo strategy:** port one legacy strategy (or use `forecast_favourite`) and run it over the
   loaded cities with `--save`, so it shows in the UI.

Legacy material to rebuild from (reference only; `legacy/` is never edited):
- `legacy/kbt/metrics.py` (149 lines): realized metrics (win rate, profit factor, expectancy, max
  drawdown, per-trade Sharpe, intraday mark-to-market drawdown).
- `legacy/kbt/strategies/midday_fade.py` (the benchmark: fade the favourite by buying NO, H hours
  before close), `highest_temp.py` (simple fade of the favourite), `armed_fade.py`.
- `legacy/backtester.html` (1,691 lines; LightweightCharts price/volume chart plus Chart.js) and
  `legacy/server.py` (websocket streaming backtests), `legacy/sweep.py` (parameter grid).
- `legacy/README.md` (metrics reference, how to read results).
- Legacy fill/contract bugs already fixed in the new engine: unlimited size, naked shorts, the `less`
  bracket off by one, float money. Don't port those.

Suggested first step: plan with the user (decisions on mark convention, Sharpe period, VaR method,
which demo strategy, and whether the visualiser extends the existing `ui/` or ports the legacy page
look). Then split into small units: metrics SQL/Python → strategy port → saved demo runs → UI pages.

## 7. Useful commands
- UI: `uv run python -m ui.serve` → http://127.0.0.1:8765
- Backtest (throwaway): `uv run python -m backtest.run_all --db kalbt`; keep the runs: add `--save`
  (back up first).
- Reconcile or portfolio-check a run: `python -m recon.check_run <id>` / `uv run python -m portfolio.check_run <id>`
- Every suite (the last full pass was green): see the test list in the plan file
  `/home/kondr/.claude/plans/can-we-test-the-lazy-dusk-of-b4d8bdb5-4be3-489e-aada-3cb6ec7bd71b.md`, step 1.
- Progress logs: `.claude/overnight/reports/track-{a,b,c}.md` (local only). Unit status:
  `.claude/specs/STATUS.md`.
