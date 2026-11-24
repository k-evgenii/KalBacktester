# B014 — First live run of the matching engine

Track: B. Overnight queue item 1 (`.claude/overnight/PLAYBOOK.md` section 7, user-approved for
the night of 2026-11-24). Specs written inside the overnight queues count as user-approved.

## Goal
Run the matching engine (B012) once on the live database with a fixed, hand-written list of
intents on the real November week, then reconcile it (B013); reconciliation must PASS. This is
the first write to the live trading tables, so it is rehearsed first on a restored copy.

## Not built
No strategy, no P&L, no new engine or reconciliation code. No change to any existing file.

## Files
- `backtest/b014_intents.csv` (new): the intents below, exactly.
- `tests/backtest/__init__.py` (new, empty), `tests/backtest/check_b014_dry_run.py` (new, qa-tester).

## The intents (event `KXHIGHNY-26NOV18`, settled high 64 °F; NWS high at 248 PM LST →
## lock 2026-11-18 19:00 UTC)

CSV columns: `decided_at,market_ticker,side,action,quantity,reason`; `decided_at` in ISO format
with `+00:00`. Rows in this order:

| # | decided_at (UTC) | market | side | action | qty | reason text | Expected outcome |
|---|---|---|---|---|---|---|---|
| 1 | 2026-11-17T14:00 | KXHIGHNY-26NOV18-B64.5 | yes | buy | 5 | before open | rejected `market_not_open` (fill 14:01 < open 2026-11-17 15:00) |
| 2 | 2026-11-18T14:00 | KXHIGHNY-26NOV18-B64.5 | yes | buy | 50 | buy the winner | filled 50 at the YES ask in force at 14:01 |
| 3 | 2026-11-18T14:00 | KXHIGHNY-26NOV18-B62.5 | yes | buy | 100 | partial fill | filled 71 + rejected `volume_cap` (10 % of 716.16 = 71) |
| 4 | 2026-11-18T14:00 | KXHIGHNY-26NOV18-B60.5 | no | buy | 20 | buy NO | filled 20 at 1 − YES bid in force at 14:01 |
| 5 | 2026-11-18T14:00 | KXHIGHNY-26NOV18-T65 | yes | sell | 5 | nothing held | rejected `nothing_to_sell` |
| 6 | 2026-11-18T17:00 | KXHIGHNY-26NOV18-B62.5 | yes | sell | 71 | early exit | filled 71 at the YES bid in force at 17:01 |
| 7 | 2026-11-18T17:00 | KXHIGHNY-26NOV18-B64.5 | yes | buy | 10 | add to winner | filled 10 at the YES ask in force at 17:01 |
| 8 | 2026-11-18T17:00 | KXHIGHNY-26NOV18-B60.5 | yes | buy | 5 | opposite side | blocked `opposite_position` |
| 9 | 2026-11-18T19:30 | KXHIGHNY-26NOV18-B64.5 | yes | buy | 5 | after the high | blocked `after_daily_high` (fill 19:31 ≥ lock 19:00) |

Expected end state: B64.5 net +60 held to settlement (result yes → payout 60); B60.5 net −20
held to settlement (result no → payout 20); B62.5 flat (no settlement row); snapshot rows for
B64.5 and B60.5 on 2026-11-18 (both settle on 2026-11-19, so no snapshot that day).

Quote and volume figures above were read from the live database by the lead; the dry run is the
authority. If any expected outcome differs in the dry run, stop and report — do not edit the
intents to make them pass.

## Agreed tests (dry run)
`tests/backtest/check_b014_dry_run.py`, plain script, `[PASS]`/`[FAIL]` per test, exit 1 on any
failure, run with `uv run python -m tests.backtest.check_b014_dry_run`:
- Create a throwaway database `kalbt_b014_dry` (autocommit `CREATE DATABASE`), restore the most
  recent dump from `/home/kondr/KalBacktester/backups/db/` into it
  (`docker exec -i kalbt-recon-db pg_restore -U <user> -d kalbt_b014_dry --no-owner` with the
  dump on stdin; the user name is read inside the container from `$POSTGRES_USER`), and drop
  the database in a `finally`.
1. Running `match_intents` on `kalbt_b014_dry` with the CSV succeeds and returns a run id.
2. Each of the 9 intents has exactly the expected outcome rows and reason codes from the table
   (outcome, reason_code, filled quantity; prices are checked by reconciliation).
3. Settlements: exactly two rows, B64.5 net 60 payout 60, B60.5 net −20 payout 20.
4. `check_run` on that run returns verdict PASS with all 13 checks passing.
5. The live database's trading tables have the same row counts before and after the script.

## Live run (lead only, after the dry run passes and the reviewer approves)
1. Backup: `docker exec kalbt-recon-db sh -c 'pg_dump -Fc -U "$POSTGRES_USER" "$POSTGRES_DB"' >
   /home/kondr/KalBacktester/backups/db/<UTC>_before-B014-live-run.dump`; check it is non-empty.
2. Confirm the live trading tables are still empty.
3. `uv run python -m engine.match_intents backtest/b014_intents.csv --strategy b014_first_live_run`
4. `uv run python -m recon.check_run <run_id>` → PASS.
5. Record the run id, counts and the reconciliation output in the progress file and the commit.

## Done when
Dry-run script all PASS; reviewer APPROVE; live run done and its reconciliation PASS.
