# Status board

Numbering from 2026-11-24: units are A## (Track A, next A018), B## (Track B, next B014), C## (Track C, C01 done).
Overnight plan: `.claude/overnight/PLAYBOOK.md`.

Edited by the lead chat only. States: `spec` (being written) → `approved` → `building` →
`review` → `ready` (to merge) → `done` (merged, tagged).

## Phase 0 — foundation

| # | Unit | State | Notes |
|---|---|---|---|
| 0.1 | Tag `v1.0.0`, move existing code to `legacy/` | moved, not committed | strategies stay private; tag not pushed |
| 0.2 | Flat script layout + `schema.sql` agreed | not started | |
| 0.3 | Postgres in Docker + `load_fixture.py` | not started | dry run of the full cycle |
| 0.4 | Agent files, spec template, this board | done | |
| 0.5 | Specs for all Phase 1 units | not started | |

## Phase 1 — build

| # | Track | Unit | Branch / chat | State |
|---|---|---|---|---|
| 002 | A ingestion | one-week KXHIGHNY raw dump (`api_probe/kalshi/dump_kxhighny.py`) | dev / lead chat | done, commit 230d69c |
| 003 | A ingestion | load the one-week dump into the database (`ingest/load_kxhighny_dump.py`); 5 quantity columns made numeric | dev / lead chat | done, commit 230d69c |
| 006 | A ingestion | one-week weather dump for KNYC (`api_probe/weather/dump_weather.py`) | dev / lead chat | done, commit f500ee8 |
| 007 | A ingestion | weather tables + KNYC seed (`db/schema.sql`, `db/seed_stations.sql`) | dev / lead chat | done, commit f500ee8 |
| 004 | B | ingestion QA gate in front of the KXHIGHNY loader (`ingest/qa_kxhighny_dump.py`) | track-b | done, commit 3b38375 (merged into dev) |
| 008 | B | settled markets frozen in the database (`market_guard`) | track-b | done, commit bfcb256 (merged into dev) |
| 009 | A ingestion | load the weather dump (`ingest/load_weather_dump.py`); forecast CHECKs relaxed by user decision | dev / lead chat | done, commit 8dd0c12 |
| 010 | B | QA gate: rules text must name the station and The Weather Company | track-b | done, commit 51139a8 (merged into dev, b821fb9) |
| 014 | A ingestion | station names as data + rules-wording survey | dev / lead chat | done, commit 67c899f |
| 015 | A ingestion | gate checks 22/23 read every historical wording; loader stores the source per event; June and Aug 2026 NY weeks loaded | dev / lead chat | done, commit 67c899f |
| 016 | A ingestion | numeric station check in the weather loader; weather for the June and Aug weeks | dev / lead chat | done, commit 67c899f |
| 017 | A ingestion | gate check 14 compares market times in local time | dev / lead chat | done (see git log) |
| B011 | B | bracket and event-outcome views | track-b | done, 9c0cb05 (merged into dev) |
| B012 | B | matching engine (`engine/match_intents.py`) | track-b | done, bb11d9f (merged into dev) |
| B013 | B | run reconciliation (`recon/check_run.py`) | track-b | done, a3eb737 (merged into dev) |
| C01 | C | portfolio ledger core (`db/portfolio.sql`) | track-c | done, merged into dev 6d559cc |
| C02 | C | portfolio day-by-day views | track-c | done, merged into dev 6d559cc |
| A018 | A | migrations (`db/migrate.py`) and backups (`db/backup.py`) | dev | done (see git log) |
| B014 | B | first live engine run (run 31), reconciled PASS | track-b | done, merged into dev 81e09e2 |
| A019 | A | seed every active US daily-high city (migration 0002) | dev | done (see git log) |
| A020 | A | market dump, gate and loader for any series; Chicago 16-22 Nov; weather gate hooked into the loader | dev | done (see git log) |
| A021 | A | resumable backfill runner (`ingest/backfill.py`) | dev | done, full run pending |
| A024 | A | migration 0004: saved backtest runs deletable, never updated | dev | done (see git log) |
| A022 | A | read-only query layer for UI screens 3-4 (`query/`) | dev | done (see git log) |
| A023 | A | coverage per city and week (`query/coverage.py`) | dev | done (see git log) |
| A025 | A | weather dump retries network timeouts | dev | done (see git log) |
| A026 | A | two ingest checks scoped to their own rows after the backfill (tests only) | dev | done (see git log) |
| C11 | C | hourly mark-to-market equity curve of a run (`portfolio/marks.py`) | dev | done (see git log) |
| C12 | C | run metrics: trades, daily P&L, Sharpe, drawdown, VaR, event risk (`portfolio/metrics.py`) | dev | done (see git log) |
| C13 | C | UI: analytics page of one run (`/runs/<id>/analytics`) | dev | done (see git log) |
| C14 | C | UI: a run's fills on the market price chart (`/runs/<id>/events/<event>`); follow-up: guard an event with orders but no fills and no quotes | dev | done (see git log) |
| | A ingestion | `ingest_kalshi.py` | | not started |
| | A ingestion | `ingest_forecasts.py` | | not started |
| | A ingestion | `ingest_settlement.py` | | not started |
| | B backtest | `build_features.py` | | not started |
| | B backtest | `run_backtest.py` | | not started |
| | C accounting | `portfolio.py` | | not started |
| | C accounting | `reconcile.py` | | not started |
| | C accounting | `daily_report.py` | | not started |

## Phase 2 — integration

| Unit | State |
|---|---|
| End-to-end on one city, trade list compared with legacy `v1.0.0` | not started |
| All cities, docs pass, tag | not started |
