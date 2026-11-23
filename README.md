# KalBacktester

**Goal:** Kalshi weather-markets backtester, being rebuilt on PostgreSQL and Polars.
**Current stage:** Branch `track-b` (database track). Latest commit: "Add the reconciliation database schema and the build-test-review agent loop"; before it, the v1.0.0 engine was moved into `legacy/`.

## Folders

| Folder | Description |
|---|---|
| `.claude/` | Working rules (`CLAUDE.md`), agents, specs and changelog |
| `db/` | Reconciliation database schema (PostgreSQL 16); see `db/README.md` |
| `tests/` | Tests; currently `tests/db/test_schema.sql` for the schema |
| `legacy/` | The v1.0.0 engine, kept as read-only reference |
| `data/` | Local data, not in git |

Top-level files: `docker-compose.yml` (the database container), `pyproject.toml`, `uv.lock`, `.python-version`.

How work is done (roles, spec, build loop, branches, safety): see `.claude/CLAUDE.md`.
