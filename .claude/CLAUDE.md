# KalBacktester — working rules

Kalshi weather-markets backtester. These rules override any agent file or house style that
disagrees with them.

## 1. Simplicity rules

1. **Simplest version that is still efficient.** Plain functions in a readable script are the
   default.
2. **No abstraction for a single use.** No class, base class, registry, wrapper or config
   object unless a second real caller exists today. Code moves into a shared module when it
   is reused, not before.
3. **If a simpler option works almost as well, keep only the simpler one.** No second variant
   behind a flag.
4. **Many small tidy scripts are good.** One job per file, easy to read, run and inspect on
   its own. Splitting for readability is welcome; splitting to add layers of indirection is
   not.
5. **Readability beats cleverness.** No dense one-liners or over-tight loops that are hard to
   debug. Use vectorised Polars where it is also the clear way to write it.
6. **Nothing "for later".** No unused parameters, placeholder folders or stub files.

## 2. Who does what

- **The user and the lead model (the main session) plan together.** The lead never starts
  coding a piece on its own.
- **Sonnet workers write the code** from the agreed spec. The lead writes specs, briefs the
  workers, reviews diffs and runs checks.
- **The user and the lead design every test and smoke test together.** Workers may not
  invent, edit or weaken them.

## 3. The spec is the prompt

A worker knows only what the spec says, so each unit of work gets one spec containing:

- **Goal and non-goals:** two sentences on what it does, and what is deliberately not built.
- **Files:** the exact paths the worker may create or change; everything else is off limits.
- **Interfaces:** function signatures, table and column names, DataFrame schemas, units.
- **Hard parts written out by the lead:** pseudo-code or real code for anything non-obvious
  (fee rounding, average-cost position updates, reconciliation joins). The worker transcribes
  it and does not design it.
- **The agreed tests:** exact inputs and expected outputs.
- **Legacy reference:** the file and lines being rebuilt.
- **Done when:** the commands that must pass.
- **If unclear:** stop and report, do not guess.

Keep a unit small: one worker, one script or a couple of closely related ones.

## 4. Build loop

| Step | Who | Gate |
|---|---|---|
| 1. Write the spec | user and lead | user approves the spec |
| 2. Implement | Sonnet implementer | none; parallel only when files are disjoint |
| 3. Self-check | implementer | runs the agreed tests and lint, reports output and anything unclear |
| 4. Independent review | a second Sonnet | gets only the spec and the diff, not the implementer's explanation |
| 5. Verify | lead | reads the diff and reruns the tests itself |
| 6. Fix | implementer | at most two rounds |
| 7. Changelog and version bump | release agent | user says commit, tag or push |

- **The reviewer checks three things:** what the spec asked for and is missing, what was
  built that the spec did not ask for, and breaches of section 1. It changes nothing.
- **Two failed fix rounds mean the spec is wrong.** Go back to step 1 with the user instead
  of patching further.
- **The reviewer sees no explanation on purpose.** An agent handed the author's reasoning
  tends to confirm it.

## 5. Tracks and chats

- **Scripts talk to each other only through Postgres tables.** `schema.sql` is the shared
  contract and is changed only in the lead chat with the user. A track that needs a schema
  change stops and asks.
- Three tracks with disjoint files: A ingestion, B backtest, C accounting. Progress is in
  `.claude/specs/STATUS.md`, edited by the lead chat only. Specs follow `.claude/specs/TEMPLATE.md`.
- Each extra chat works in its own git worktree on its own branch
  (`git worktree add ../KalBacktester-track-b -b track-b dev`). At most three chats.
- **Nobody works on `main`.** No commits, edits or merges there during development. `main`
  only receives code the user has approved, pulled in from `dev` on the user's instruction.
- `dev` is the working branch. Track branches are cut from `dev` and merged back into `dev`
  by the lead chat only. After each merge it runs the whole test suite on `dev`.
  `dev` is always green: a merge that breaks it is reverted and the unit reopened.
- Any agent that finds itself on `main` stops and reports before changing anything.
- One unit = one commit = one tag. Agents: `implementer`, `reviewer`, `release-keeper`
  per unit; `annotator` and `readme-maintainer` once, in the final docs pass.

## 6. Safety

- Workers never commit, tag or push, and never touch `.env`, `legacy/` or `data/`.
- Two workers never edit the same file.
- Agreed tests are frozen for workers.
- Destructive database actions happen only on the user's instruction.
- Orchestration runs from the main session, not from git hooks, so nothing fires unseen.
