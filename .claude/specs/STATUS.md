# Status board

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
| 002 | A ingestion | one-week KXHIGHNY raw dump (`api_probe/kalshi/dump_kxhighny.py`) | dev / lead chat | approved by reviewer, verified by lead; not committed |
| 003 | A ingestion | load the one-week dump into the database (`ingest/load_kxhighny_dump.py`); 5 quantity columns made numeric | dev / lead chat | approved by reviewer, verified by lead; not committed |
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
