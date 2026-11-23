# KalBacktester

Where the code lives and how the repository is managed. Each folder's own `README.md` explains
its contents.

## Where things are

| Path | What is there |
|---|---|
| `db/schema.sql` | Reconciliation database schema (PostgreSQL 16); guide in `db/README.md` |
| `docker-compose.yml` | The database container (`kalbt-recon-db`, port 5433) |
| `tests/db/` | Schema tests |
| `legacy/` | The v1.0.0 engine and its scripts, kept as read-only reference; old README in `legacy/README.md` |
| `.claude/CLAUDE.md` | Working rules: roles, specs, build loop, branches, safety |
| `.claude/agents/` | Agent definitions (implementer, qa-tester, reviewer, release-keeper, docs agents) |
| `.claude/specs/` | One spec per unit of work, the spec template, and `STATUS.md` (progress board) |
| `.claude/CHANGELOG.md` | One entry per finished unit; the version number is in `pyproject.toml` |
| `data/` | Local data, not in git |
| `.env` | Local credentials, not in git |

## Branches

| Branch | Use |
|---|---|
| `main` | Approved code only. Nobody commits to it directly; it receives `dev` on the owner's instruction. |
| `dev` | Integration branch. Track branches are merged into it. |
| `track-*` | One per work track (e.g. `track-b`, the database), cut from `dev`. |

Work on a track in its own folder, so branches never share a working copy:

```bash
git worktree add ../KalBacktester-track-b -b track-b dev   # create
git worktree list                                          # see all
```

A branch checked out in a worktree cannot also be checked out in the main folder; open the
worktree folder instead.

## Common tasks

| Task | Command / place |
|---|---|
| Start the database, connect, run its tests | see `db/README.md` |
| Add a unit of work | copy `.claude/specs/TEMPLATE.md` to `.claude/specs/NNN-name.md`, then follow the build loop in `.claude/CLAUDE.md` |
| Check progress | `.claude/specs/STATUS.md` |
| Releases | tags `vMAJOR.MINOR.PATCH`; `v1.0.0` is the legacy engine |
