# C07 — UI tidy: table helpers move to layout

Track: C (UI lead). Follow-up named by the C06 reviewer.
STATUS: approved (overnight playbook: specs inside the queue count as user-approved).

## Goal
`table()` and `td()` in `ui/runs.py` now have a second caller (`ui/portfolio.py`), so they
move to the shared module `ui/layout.py` (CLAUDE.md §1.2: code moves to a shared module when
it is reused). Behaviour and HTML output do not change at all.

## Not built
No other refactor, no renames, no new helpers, no CSS or markup change.

## Files
- `ui/layout.py` (edit: add `table` and `td`, moved verbatim with their docstring)
- `ui/runs.py` (edit: delete the two functions, import them from `ui.layout`)
- `ui/portfolio.py` (edit: import `table, td` from `ui.layout` instead of `ui.runs`)

## Interfaces
Unchanged: `table(head, rows, attrs="")`, `td(content, css="")`, same bodies, now in
`ui.layout`. `ui/portfolio.py` no longer imports anything from `ui.runs`.

## Hard parts
None: a verbatim move.

## Agreed tests
No new tests. The existing suites are the check, unchanged:
1. `PYTHONPATH=. uv run python tests/ui/check_runs.py` → all PASS (32).
2. `PYTHONPATH=. uv run python tests/ui/check_portfolio_page.py` → all PASS (28).
3. `grep -n "from ui.runs" ui/portfolio.py` finds nothing; `grep -n "^def table\|^def td" ui/`
   finds them only in `ui/layout.py`.

## Test files
None.

## Legacy reference
None.

## Done when
- The three checks above hold, and `uvx ruff check ui/` is clean.

If anything here is unclear or looks wrong: stop and report, do not guess.
