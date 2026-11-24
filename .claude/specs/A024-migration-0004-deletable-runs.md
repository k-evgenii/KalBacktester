# A024 — Migration 0004: saved backtest runs can be deleted (DRAFT)

Track: A. Status: APPROVED by the user 2026-11-24 (scope as below). Applied after B019 is merged.

## Why
User decision (Track B's chat, 2026-11-24): backtest runs are throwaway by default. A saved run is a
log for later analysis: it may be deleted, but never edited. The strict append-only rules of spec 001
are meant for a future real-orders database, which does not exist yet.

## Scope (from Track B's NEED)
- **Drop:** the DELETE and TRUNCATE guards on `order_intent`, `order_outcome`, `settlement` and
  `position_snapshot`, and the DELETE branch of `run_guard`, plus `run`'s TRUNCATE guard.
- **Keep:** UPDATE stays forbidden on all of them, and `run` keeps "only status/finished_at may change".
- **Unchanged:** `candle`, `market` (settled freeze) and every weather table stay fully protected.

## Tests that change together with it
`tests/db/test_schema.sql`:
- Test 7, the DELETE and TRUNCATE asserts on the four trading tables (≈ lines 454–499);
- the run guard's "DELETE on run refused" and "TRUNCATE run refused" (≈ lines 530–537).
These become "DELETE/TRUNCATE allowed", with UPDATE still refused.

Unaffected (checked by Track B): recon and portfolio tamper tests (they disable the triggers), the
engine, UI and ingest tests.

## User decisions (2026-11-24)
- Deleting a saved run: one script (`backtest/delete_run.py`, Track B) removes the run and its intents,
  outcomes, settlements and snapshots in one transaction. No ON DELETE CASCADE in the schema.
- Runs 31 and 32 are kept.
