# C10 — UI screen 5: data coverage per city and week

Track: C (UI lead). Fifth UI unit of the brief `.claude/specs/C04-ui-brief.md`.
STATUS: approved (overnight playbook). Names fixed against Track A's A023 (merged on dev, tag
night/A023): `query/coverage.py`.

## Goal
One read-only page showing how complete the data is: a city × week grid with one status chip per
cell, a one-line summary of the counts, and a table of every cell that is not OK with all its
numbers, so a gap or a gate warning is found at a glance.

## Not built
No drill-down pages (a cell links to the C08 days page of that city), no filters, no HTMX, no
refresh, nothing written to the database.

## Files
- `ui/coverage.py` (new): the page
- `ui/serve.py` (edit: route `/coverage`, nothing else)
- `ui/layout.py` (edit: a third nav link "Coverage" → `/coverage`, active when
  `active == "coverage"`; nothing else)
- `ui/static/app.css` (edit: append the grid rules)

## Interfaces

Data only from Track A's `query.coverage` (`from query import coverage as cov`):
`cov.coverage(conn)` (default window 2026-08-24 – 2026-11-22) → list of dicts ordered by
`underlying_id` then `week_start`: `underlying_id, series_ticker, station_id, week_start (date, a
Monday), events, markets, bars, nws_highs, kalshi_highs, numeric_mismatches, gate_warnings (int,
or None when the week's dump report is missing), status` (`OK | Check | RED`). Every city has
all 13 window weeks (an empty week is RED); weeks outside the window appear only where a city has
events there (e.g. NYC_HIGH 2026-06-01). `gate_warnings` does not affect `status`.
`gate_warnings` reads dump reports from the checkout's `api_probe/kalshi/`; where they are absent
(e.g. a worktree or a test) it is None and the page shows the missing mark.

```python
# ui/coverage.py
def coverage_page(conn) -> str
def status_kind(status: str) -> str     # OK -> "ok", Check -> "warn", RED -> "red", else ValueError
```

Page (title "Coverage"; nav active "coverage"), sections in order:
1. **Summary** one line: `<n> city-weeks · <a> OK · <b> Check · <c> RED · weeks <first> – <last>`
   (counts follow the rows returned). No rows → "No coverage data yet." and nothing else.
2. **Grid**: a table, rows = cities (sorted by `underlying_id`), columns = every distinct
   `week_start` (oldest first, header `YYYY-MM-DD`, mono). Cell = `chip(status, status_kind(status))`
   wrapped in a link to `/markets/<underlying_id>`, with a `title` attribute
   `"<events> events · <markets> markets · <bars> bars · NWS <nws_highs> · Kalshi <kalshi_highs>
   · gate <gate_warnings or –> · numeric <numeric_mismatches>"`. A city-week with no row → the
   missing mark (the city or week is not loaded).
3. **Needs attention**: one table of the rows whose status is not `OK`, ordered RED first, then
   Check, then city, then week. Columns: City (mono), Week (mono), Status chip, Events, Markets,
   Bars, NWS highs, Kalshi highs, Gate warnings (missing mark when None), Numeric mismatches
   (counts, right-aligned).
   None → "Every loaded city-week is OK."

## Hard parts
None beyond the grid pivot: build `cells = {(underlying_id, week_start): row}`, `cities =
sorted({r["underlying_id"]})`, `weeks = sorted({r["week_start"]})`, then one `<tr>` per city
with one `<td>` per week looked up in `cells`.

CSS to append (as written):

```css
.cov-grid td { text-align: center; padding: 4px; }
.cov-grid a { text-decoration: none; }
.cov-grid .chip { font-size: 11px; }
```

## Agreed tests

`tests/ui/check_coverage.py`, house style, `PYTHONPATH=.`. Throwaway database
`kalbt_ui_coverage_<pid>` from `db/schema.sql` and `db/portfolio.sql`, with a hand fixture of
three cities (stations, underlyings, series) inside the default window: one city-week with 7
settled events, matching NWS and Kalshi highs and candles (OK); one with 7 events and one
Kalshi/NWS disagreement (Check); everything else empty (RED); plus one event for one city on
2026-06-01 (an extra week only that city has, so the other cities show the missing mark there).
Expected statuses and numbers are read from `cov.coverage(conn)` on the same database, plus the
hand statuses above. Server via `make_server(0, db)`.

1. `/coverage` → 200; summary counts match the fixture; the nav link "Coverage" is current.
2. The grid has one row per city and one column per week; each cell's chip word and kind match
   its status; the missing city-week shows the missing mark; each cell links to
   `/markets/<city>`; the `title` carries the cell's numbers.
3. "Needs attention" lists RED before Check, with every number column right-aligned and equal to
   the fixture.
4. `status_kind`: OK → ok, Check → warn, RED → red; "other" raises `ValueError`.
5. An empty coverage result → "No coverage data yet." and no grid.
6. Escaping of an `underlying_id` with `<>&"` (in the throwaway database).
7. Live `kalbt` row counts in `run`, `order_intent`, `order_outcome` unchanged; a read-only GET
   of `/coverage` on live returns 200 and shows `NYC_HIGH` with an OK chip in the 2026-11-16
   column (no other count assertions: the backfill grows the data).

## Test files
- `tests/ui/check_coverage.py` (new)

## Legacy reference
- None. Design (read-only): `/home/kondr/KalBacktester/.claude/agents/daylight-graphite.md`
  (status is a chip with a dot and a word; one status per cell; counts follow the rows shown).

## Done when
- `PYTHONPATH=. uv run python tests/ui/check_coverage.py` exits 0, only `[PASS]` lines.
- The other UI suites still exit 0 (run separately); `uvx ruff check ui/ tests/ui/` clean;
  clean-export run before READY.

If anything here is unclear or looks wrong: stop and report, do not guess.
