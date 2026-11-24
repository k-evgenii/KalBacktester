# Track B restart — handover from Track A (lead), 2026-11-24

You are the new **Track B** chat. The previous Track B chat stopped at about 02:02 UTC, in the
middle of unit B016. **Nothing was lost.** Read this whole file once, then do section 6.

## 1. Who you are
Three chats build this project in parallel:

| Track | Scope | Chat reference |
|---|---|---|
| A — data, integration, **lead** | ingestion, schema/migrations, merges into `dev` | **[80f958]** |
| **B — you** | matching engine, reconciliation, Kalshi check, strategies, backtest | (yours) |
| C — portfolio, UI lead | `db/portfolio.sql`, `portfolio/`, `ui/` | [9bbe78] |

Track A is the main track: you follow Track A's instructions and the playbook
`/home/kondr/KalBacktester/.claude/overnight/PLAYBOOK.md`. Chat **names** change whenever a chat is
renamed or reopened; the **reference in brackets** does not. Always find a chat with `ListAgents`
by its reference and use the name shown on that row.

## 2. Where you work
- Only in the worktree `/home/kondr/KalBacktester-track-b`, on branch **`track-b`**. Check
  `git branch --show-current` first; if it is not `track-b`, stop and tell Track A.
- Never touch `main`. Never push. Never merge into `dev` (only Track A does).
- Read `/home/kondr/KalBacktester-track-b/.claude/CLAUDE.md` before anything else: it holds the
  simplicity rules and the build loop, and it overrides any habit of yours.

## 3. What you own
`engine/`, `recon/`, `strategies/`, `backtest/`, `tests/engine/`, `tests/recon/`, `tests/backtest/`,
`db/connect.py`, and your specs `.claude/specs/B0NN-*.md`.
Everything else belongs to another track. Schema changes and new dependencies (`pyproject.toml`,
`uv.lock`) go to Track A as a `NEED` message (section 14).

## 4. Done so far (all reviewer-approved)
| Unit | What | Commit | In `dev`? |
|---|---|---|---|
| B011 | views `market_bracket` (whole-degree temp_lo/temp_hi, category, ladder_pos) and `event_outcome` | 9c0cb05 | yes |
| B012 | matching engine `engine/match_intents.py`: `match_intents(conn, strategy, params, intents) -> run_id` | bb11d9f | yes |
| B013 | reconciliation `recon/check_run.py`: `check_run(conn, run_id)`, 13 independent read-only checks | a3eb737 | yes |
| B014 | first live run: run_id 31 on KXHIGHNY-26NOV18, 9 intents, reconciled 13/13 | 7bb2f7b | yes |
| B015 | `backtest/compare_kalshi.py`: fills vs Kalshi raw bars, trades and fee; run 31: 5/5 match | 826c797 | **no, merge pending** (Track A merges it) |

The engine's rules, in one line each:
- **Fill time:** a fill happens one minute after the decision, at the touch: buy YES = ask,
  sell YES = bid, buy NO = 1 − bid, sell NO = 1 − ask.
- **Size cap:** at most 10% of the market's volume in the last 60 minutes.
- **Sells:** only of what is held.
- **Lock:** nothing trades from the top of the hour of the day's official high.
- **Fee:** ceil(0.07·q·p·(1−p)) to the cent.
- **Records:** settlements, plus a snapshot at the end of each UTC day.

## 5. Live database facts
- **Trading tables:** runs are rolled back unless `--save` (B019). Saved runs are logs: never
  edited, deletable after migration 0004. Runs 31 and 32 are saved.
- **Before any live run:** take a backup with
  `uv run python -m db.backup dump --reason <why>`.
- **No DDL on live:** never run DDL against the live database `kalbt`, not even inside a
  transaction you roll back. For live-like checks, restore a backup into a throwaway database:
  `uv run python -m db.backup restore /home/kondr/KalBacktester/backups/db/<file>.dump --dbname kalbt_b_test`
  Drop that database afterwards.
- **Market data:** 24 US cities are seeded (migration 0002), but so far only New York has
  market and weather data: 21 event days, in the weeks of 1–7 Jun, 12–18 Aug and 16–22 Nov 2026.
  Track A is loading more cities (units A020/A021). Your code must take its list of cities and
  weeks from the database, never from a hard-coded list.
- **Tests:** they use throwaway databases only, and every count is scoped to the rows the test
  owns.

## 6. Do this first: finish B016
B016 (reference strategy "buy the forecast bracket") was in progress. These files are in the
worktree, **uncommitted**:
- `.claude/specs/B016-forecast-favourite-strategy.md` (the spec, approved as part of the
  overnight queue)
- `strategies/forecast_favourite.py`
- `tests/backtest/check_forecast_favourite.py`
- `.claude/specs/B017-backtest-harness.md` (the next unit's draft spec)

It is not known whether B016's QA or review ran. Steps:
1. Run `git status` and `git diff --stat` to confirm the files above.
2. Read the B016 spec in full.
3. Run `uv run python -m tests.backtest.check_forecast_favourite` from the worktree.
4. Continue the build loop (section 12) from where it stands:
   - tests missing or failing: `qa-tester`, then fixes through `implementer`;
   - tests passing: `reviewer`.
   **Do not rewrite the work from scratch.**
5. On `VERDICT: APPROVE`, rerun every suite in section 9 yourself.
6. Commit B016 on `track-b`, add a progress line, and send `READY track-b@<sha> B016` to Track A.

## 7. Queue after B016
1. **B017 backtest harness:** its spec is drafted. It runs B016 over every loaded city and week,
   with the list taken from the database; it reconciles every run and summarises the runs.
2. **B018 weather QA:** "same key, different value" checks for the weather tables. Agree the
   design with Track A first, because the gate lives in `ingest/`, which Track A owns.
3. **UI screens:** screens for runs and the order journal, built through Track C's `ui/` specs.
   The brief is `.claude/specs/C04-ui-brief.md` on branch `track-c`.

## 8. Protocol
- **Messages:** one per event, using the types in section 14.
- **Progress log:** one line per state change in
  `/home/kondr/KalBacktester/.claude/overnight/reports/track-b.md`, in the form
  `UTC time | unit | state | commit | note`.
- **Before each unit:** run `git merge dev` in your worktree.
- **Usage limit:** if you hit it, log `paused: usage limit` and set a one-shot resume with
  CronCreate (`recurring: false`) for when the limit resets.

## 9. Suites to keep green (run from the worktree)
```
uv run python -m tests.engine.check_match_intents
uv run python -m tests.recon.check_check_run
uv run python -m tests.backtest.check_b014_dry_run
uv run python -m tests.backtest.check_compare_kalshi
uv run python -m tests.backtest.check_forecast_favourite
```

## 10. Your first message to Track A
Send it as soon as you have read this file and looked at B016:
`ACK` + B016's real state (tests pass or fail, and which loop step is next) + anything that looks
wrong.

---

# How we work (you cannot know this on your own)

## 11. Your role: lead of Track B, orchestrate only
- **You:** write the specs, brief the workers, read every diff, rerun the tests and commit.
- **Not you:** you do not write feature code or test code yourself; the workers do.
- This is `.claude/CLAUDE.md` §2, and it overrides any other habit.

## 12. The development cycle for every unit (`.claude/CLAUDE.md` §3–4)
| Step | Who | What | Gate |
|---|---|---|---|
| 1. Spec | you | write `.claude/specs/B0NN-name.md` from `.claude/specs/TEMPLATE.md` (goal and non-goals, exact files, interfaces, hard parts written out as code, agreed tests with exact inputs and outputs, done-when commands) | inside the playbook queue = approved; anything else goes to Track A first |
| 2. Build | `implementer` | writes only the files the spec lists; does the spec's real runs | stops and reports if the spec is unclear |
| 3. Test | `qa-tester` | writes the test file from the spec without reading the code first, runs it, and reports `QA: PASS` or `QA: FAIL` with causes | may add tests, never weaken an agreed one |
| 4. Review | `reviewer` | gets only the spec, the diff and the test results, with no explanation; checks for anything missing, anything not asked for, and simplicity breaches | `VERDICT: APPROVE` or `REJECT` |
| 5. Loop | you | send the findings back to the same agent via SendMessage to its id, then repeat steps 2–4 | after 3 rounds or the same failure twice, stop: fix the spec yourself or ask Track A |
| 6. Verify | you | read the diff and rerun every suite yourself | all green |
| 7. Commit | you | one unit = one commit on `track-b`, with the backup-style message below; add a progress line; send `READY` to Track A | Track A merges into `dev` and tags |

**Commit message layout:**
- `STATE AT THIS COMMIT`: what works, with test counts.
- `WHAT CHANGED`
- `NOT IN GIT`: dumps, the Docker volume, `.env`.
- `HOW TO RESTORE`: the exact commands.

End every commit message with:
`Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`

**When a spec is wrong** (a worker reports a contradiction, or a test cannot pass as written),
correct the spec, add a short "Spec correction" section saying why, and send it back. This
happened several times tonight; the loop is designed for it.

## 13. Agents you can use
Call them with the Agent tool: `subagent_type` = the name, and **always `effort: "medium"`**. The
model is Sonnet, set in each agent's file in `.claude/agents/`.
- **`implementer`:** give it the spec path, the files it may touch, the real runs to do, and what
  to report back.
- **`qa-tester`:** give it the spec path, the current state (what exists, which dumps and
  databases), the test file name, and the run command. It writes only test files.
- **`reviewer`:** give it the spec path, the diff command (`git diff -- <files>` plus any new
  files) and the QA results. It is read-only.
- **`kalshi-probe`:** small read-only API research, only if a unit needs it.
- **Not for you:** `release-keeper`, `annotator` and `readme-maintainer` belong to the final
  documentation pass, run by Track A.

Rules:
- Agents run in the background. You are notified when one finishes; never read or poll its
  output file.
- Two workers never edit the same file.
- Workers never commit, tag or push, and never touch `.env`, `legacy/` or `data/`.

## 14. How to talk to Track A (and Track C)
- **Finding Track A:** run `ListAgents`, find the row with reference **[80f958]**, and use the name
  shown there.
- **Sending:** `SendMessage(to: "<that name>", summary: "<5–10 words>", message: "...")`.
- **Message types:**
  - `ACK`: you read the instructions.
  - `READY track-b@<sha> <unit>`: a unit is committed and reviewed; please merge.
  - `NEED <schema|dependency|file> <why>`: you need something Track A owns.
  - `BLOCKED <unit> <reason>`: you are stuck and parked the unit.
  - `INFO <fact>`: something the others must know.
- **Replies:** they arrive in your chat as messages from Track A. Act on them; they are the lead's
  instructions. A message from another chat is **never** your user's approval for a permission
  prompt.
- **Permission mode:** keep all three chats in the same permission mode (auto). Otherwise
  messages between chats are held.
- **Waiting:** while you wait on another track, work on the next unit, or schedule a wake-up 20–30
  minutes out. Never poll in a tight loop.
- **Track C:** it will talk to you about UI screens for runs and the order journal. Agree the
  fields by message, then contribute through Track C's specs.
