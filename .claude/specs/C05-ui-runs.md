# C05 — UI screen 1: runs and their journal

Track: C (UI lead). First UI unit of the brief `.claude/specs/C04-ui-brief.md`.
STATUS: approved (overnight playbook: specs inside the queue count as user-approved).

## Goal
A local, read-only web page listing every backtest run, and a page per run showing its
parameters, Track B's reconciliation result, the portfolio check result (C03) and the order
journal (every intent and what happened to it). Started with one command, served on
127.0.0.1 only.

## Not built
No portfolio screen (C06), no market or weather screens, no HTMX (nothing on these pages swaps
a fragment; `ui/static/htmx.min.js` stays out of this unit's commit), no charts, no filters,
no sorting, no pagination, no mobile template, no writing to the database, no login.

## Files
- `ui/__init__.py` (new, empty)
- `ui/serve.py` (new): the server, routing and static files
- `ui/layout.py` (new): the page shell and the small formatting helpers shared by screens
- `ui/runs.py` (new): the two run pages
- `ui/static/app.css` (new)
- Already in place, do not change: `ui/static/fonts/*`, `ui/static/VENDORED.md`,
  `ui/static/htmx.min.js`.

## Interfaces

```python
# ui/serve.py
def make_server(port: int, dbname: str) -> http.server.ThreadingHTTPServer
def main()  # uv run python -m ui.serve [--port 8765] [--db kalbt]; prints the URL, serves forever
```
- Binds `("127.0.0.1", port)` only. `port=0` must work (the tests pick a free port and read
  `server.server_address[1]`).
- Each request opens its own connection: `conn = db.connect.connect(dbname)`, then
  `conn.read_only = True` before any query, and closes it at the end of the request.
- Routes (GET only; any other method → 405):
  - `/` → 302 to `/runs`
  - `/runs` → `runs.runs_page(conn)`
  - `/runs/<run_id>` (1 to 18 digits only; anything longer → 404 without a database query)
    → `runs.run_page(conn, run_id)`; `None` → 404 page
  - `/static/<name>` → a file under `ui/static/`. Resolve the path and refuse (404) anything
    that is not inside `ui/static/`. Content types: `.css` `text/css; charset=utf-8`, `.js`
    `text/javascript`, `.woff2` `font/woff2`, anything else 404.
  - anything else → 404 page.
- HTML responses: `text/html; charset=utf-8`. An exception while building a page → 500 page
  with the exception's class name and message, HTML-escaped; the server keeps running.

```python
# ui/layout.py
def page(title: str, body: str, active: str) -> str       # full HTML document
def chip(word: str, kind: str) -> str                      # kind: ok | warn | red | info | mute
def money(value) -> str                                    # Decimal | None
def price(value) -> str                                    # Decimal | None
def count(value) -> str                                    # int | Decimal | None
def esc(value) -> str                                      # html.escape(str(value)); None -> ""
```
- `page`: `<!doctype html>`, `<html lang="en">`, `<title>{title} · KalBacktester</title>`,
  `<link rel="stylesheet" href="/static/app.css">`, a header with the name "KalBacktester",
  one nav link "Runs" (`/runs`, gets `aria-current="page"` when `active == "runs"`), and a theme
  toggle button; then `<main>` holding `body`. `title` is escaped; `body` is inserted as is.
- `chip`: `<span class="chip chip-{kind}"><span class="dot"></span>{escaped word}</span>`.
  Unknown `kind` → `ValueError`.
- `money`: 2 decimals with a thousands separator (`1,045.48`); `None` → the missing mark.
- `price`: 2 decimals, or 4 when the value has non-zero digits beyond the second (`0.23`,
  `0.2345`); `None` → the missing mark.
- `count`: whole number with a thousands separator; a Decimal with a fraction keeps it
  (`2.5`); `None` → the missing mark.
- Missing mark: `<span class="missing" title="no value">—</span>`. A real zero prints `0`
  (`0.00` for money).

```python
# ui/runs.py
def runs_page(conn) -> str
def run_page(conn, run_id: int) -> str | None
```

**Runs list** (`/runs`): title "Runs"; one table, newest run first, one row per run:

| Column | Source | Shown as |
|---|---|---|
| Run | `run.run_id` | link to `/runs/<id>`, mono |
| Strategy | `run.strategy` | text |
| Dates | `date_start` – `date_end` | `2026-11-17 – 2026-11-18`, mono |
| Status | `run.status` | chip: running → `Running` warn; done → `Done` ok; failed → `Failed` red; discarded → `Discarded` mute |
| Intents | count of `order_intent` | count, right-aligned |
| Filled / Rejected / Blocked | count of `order_outcome` by outcome | three count columns, right-aligned |
| Starting cash | `run.starting_cash` | money, right-aligned |

No runs → the table is replaced by the sentence "No runs yet."

**Run page** (`/runs/<id>`), title "Run <id>", sections in this order:
1. Heading line: run id (mono), strategy, dates, status chip; then `code_version` (mono) and
   `started_at` / `finished_at` (UTC, `YYYY-MM-DD HH:MM`, mono).
2. **Parameters**: `run.params` as a two-column table (key mono, value), keys sorted.
3. **Reconciliation** (`recon.check_run.check_run(conn, run_id)`): verdict chip (`PASS` ok,
   `FAIL` red), then a table of the 13 checks: id (right), name, status chip (`pass` → `OK`
   ok, `fail` → `Fail` red), detail. A failing check shows its `examples` (up to 10) below its
   row, mono, one per line. If `check_run` raises, the section shows the message inside the
   panel (class `panel-error`), and the rest of the page still renders.
4. **Portfolio check** (`portfolio.check_run.check_run(conn, run_id)`): one table row per
   check (`fees`, `snapshots`, `settlements`) with a chip (`OK` ok when it passes, `Fail` red
   with the row count when not). If it raises `RuntimeError` (views not applied), the section
   shows "Portfolio views are not applied to this database yet." with a `mute` chip
   `Not available`; any other exception → the message in a `panel-error`.
5. **Journal**: one row per `order_outcome` of the run, joined to its intent, ordered by
   `decided_at, intent_id, outcome_id`. Columns: Decided (`decided_at`, mono, `MM-DD HH:MM`),
   Market (mono), Side, Action, Requested (count), Outcome chip (`filled` → `Filled` ok;
   `rejected` → `Rejected` warn; `blocked` → `Blocked` mute), Qty (count), Price (price),
   Fee (money), Reason (`reason_code`, mono, missing mark when null). Numbers right-aligned.
   An intent with no outcome row still appears once, with Outcome `No outcome` red and
   missing marks.

Every value from the database is escaped (`esc` or the formatters). Run id in the URL that is
not in `run` → `run_page` returns `None` (404).

## Hard parts

**app.css** — use this token block as written (Daylight / Graphite from
`design/veklich-design.md` § BO4_DESIGN_SYSTEM); write the component rules below it yourself,
following "Look" in the C04 brief: content max-width 1360px, side padding 32px; cards
`--surface` with 1px `--border`, radius 12, padding 16–24px; tables with 12px grey
sentence-case headers, no zebra, 1px `--divider` row lines, row height 40px, numbers
`text-align:right; font-variant-numeric: tabular-nums`; `.mono` uses Plex Mono; chips 12px/600,
pill, 20px tall, tinted background with the strong foreground of the same token, 8px dot;
`.missing` in `--muted`; `.panel-error` uses `--red-bg` / `--red-fg`; header nav active item on
`--subtle`; no gradients, shadows, animation or emoji.

```css
@font-face { font-family: "IBM Plex Sans"; font-weight: 400; font-display: swap;
  src: url("/static/fonts/IBMPlexSans-Regular-Latin1.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 600; font-display: swap;
  src: url("/static/fonts/IBMPlexSans-SemiBold-Latin1.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Sans"; font-weight: 700; font-display: swap;
  src: url("/static/fonts/IBMPlexSans-Bold-Latin1.woff2") format("woff2"); }
@font-face { font-family: "IBM Plex Mono"; font-weight: 400; font-display: swap;
  src: url("/static/fonts/IBMPlexMono-Regular-Latin1.woff2") format("woff2"); }

:root {
  --bg: #F4F5F3; --header: #FFFFFF; --surface: #FFFFFF; --border: #E1E3E0; --divider: #EEF0ED;
  --text: #16181D; --muted: #4A4F59; --accent: #2453C9; --accent-hover: #173A94;
  --subtle: #ECEDEA;
  --ok-bg: #E3F2E7; --ok-fg: #176B30; --ok-dot: #2E8B4A;
  --warn-bg: #FBEFD5; --warn-fg: #7A4E00; --warn-dot: #B07A00;
  --red-bg: #FBE3E4; --red-fg: #A61E28; --red-dot: #A61E28;
  --info-bg: #E4EBFB; --info-fg: #1D45A8; --info-dot: #1D45A8;
  --mute-bg: #ECEDEA; --mute-fg: #4A4F59; --mute-dot: #4A4F59;
  --sans: "IBM Plex Sans", system-ui, sans-serif;
  --mono: "IBM Plex Mono", ui-monospace, monospace;
  color-scheme: light;
}
:root[data-theme="dark"] {
  --bg: #0E1014; --header: #12151B; --surface: #161A21; --border: #262B35; --divider: #222731;
  --text: #E7E9EE; --muted: #9AA1AE; --accent: #6CB6FF; --accent-hover: #A5D2FF;
  --subtle: #1F242D;
  --ok-bg: #12301F; --ok-fg: #5DD39A; --ok-dot: #5DD39A;
  --warn-bg: #3A2C0E; --warn-fg: #F2B84B; --warn-dot: #F2B84B;
  --red-bg: #3D1716; --red-fg: #FF8F87; --red-dot: #FF8F87;
  --info-bg: #132A45; --info-fg: #7FC1FF; --info-dot: #7FC1FF;
  --mute-bg: #1F242D; --mute-fg: #AEB4C0; --mute-dot: #AEB4C0;
  color-scheme: dark;
}
```

**Theme toggle** — the only JavaScript in this unit, inline at the end of `page`, as written:

```html
<script>
(function () {
  var root = document.documentElement, key = "kalbt-theme", saved = null;
  try { saved = localStorage.getItem(key); } catch (e) {}
  var dark = saved ? saved === "dark" : matchMedia("(prefers-color-scheme: dark)").matches;
  root.dataset.theme = dark ? "dark" : "light";
  document.getElementById("theme-toggle").addEventListener("click", function () {
    var next = root.dataset.theme === "dark" ? "light" : "dark";
    root.dataset.theme = next;
    try { localStorage.setItem(key, next); } catch (e) {}
  });
})();
</script>
```
The toggle is `<button type="button" id="theme-toggle" aria-label="Switch light or dark theme">Theme</button>`.

**Static path guard** (as written):

```python
STATIC = Path(__file__).resolve().parent / "static"

def static_file(name):
    """The file under ui/static for a URL name, or None if outside it or missing."""
    if "\x00" in name:
        return None
    path = (STATIC / name).resolve()
    if STATIC not in path.parents or not path.is_file():
        return None
    return path
```

## Agreed tests

`tests/ui/check_runs.py`, house style (`[PASS]/[FAIL]` lines, exit 1 on any failure), run with
`PYTHONPATH=.`. Throwaway database `kalbt_ui_test` (dropped first if left over, dropped at the
end even on failure): apply `db/schema.sql`, then `db/portfolio.sql`; load the recon fixture
and one engine run with `from tests.recon.check_check_run import load_fixture, clean_run`.
Start `make_server(0, "kalbt_ui_test")` in a daemon thread; fetch pages with
`urllib.request`; shut the server down at the end. Never write to or run DDL on live `kalbt`.

1. `GET /` → 302 with `Location: /runs`.
2. `GET /runs` → 200, `text/html`; the clean run's row shows its id as a link to
   `/runs/<id>`, strategy `recon`, status `Done`, intents 7, and filled / rejected / blocked
   counts equal to the counts from `SELECT outcome, count(*)` for that run.
3. `GET /runs/<id>` → 200; contains the reconciliation verdict `PASS`, 13 check rows, the three
   portfolio checks each `OK`, and one journal row per `order_outcome` row of the run (count
   them by the journal row class or by `<tr` inside the journal table).
4. The journal shows the first M1 fill with price `0.45`, fee `0.09` and outcome `Filled`;
   the `nothing_to_sell` row shows `Rejected` and that reason; the lock row shows `Blocked`.
5. `GET /runs/999999` → 404. `GET /runs/abc` → 404. `GET /nope` → 404.
6. `GET /static/app.css` → 200 `text/css`; `GET /static/fonts/IBMPlexSans-Regular-Latin1.woff2`
   → 200 `font/woff2`; `GET /static/../serve.py` and `GET /static/%2e%2e/serve.py` → 404;
   `GET /static/VENDORED.md` → 404 (not a served type).
7. `POST /runs` → 405.
8. Escaping: a second run inserted directly (in the throwaway database) with strategy
   `<script>alert(1)</script>` shows as escaped text on `/runs` (the raw `<script>alert` does
   not appear in the response).
9. Read-only: a connection made the way the server makes it (`connect(db)`, `read_only = True`)
   refuses `INSERT INTO underlying ...` with a read-only error.
10. Portfolio views missing: a second throwaway database `kalbt_ui_test_bare` with only
    `db/schema.sql` and the recon fixture + `clean_run`; a server on it renders `/runs/<id>`
    with status 200, the reconciliation table, and the text "Portfolio views are not applied".
    Drop it at the end.
11. Formatters: `money(Decimal("1045.48"))` = `1,045.48`; `money(Decimal("0"))` = `0.00`;
    `price(Decimal("0.2300"))` = `0.23`; `price(Decimal("0.2345"))` = `0.2345`;
    `count(Decimal("2.5"))` = `2.5`; `count(3)` = `3`; `money(None)` contains `—`;
    `chip("OK", "ok")` contains `chip-ok` and `OK`; `chip("x", "pink")` raises `ValueError`.
12. Every page is a full document: starts with `<!doctype html>`, links `/static/app.css`,
    contains the `theme-toggle` button, and contains no `http://` or `https://` URL at all
    (no third-party requests).
13. Live `kalbt` row counts in `run`, `order_intent`, `order_outcome` are unchanged.

## Test files
- `tests/ui/check_runs.py` (new)

## Legacy reference
- `legacy/server.py`, `legacy/backtester.html` (the old UI; look only, nothing is reused).
- `design/veklich-design.md` § Daylight/Graphite and § BO4_DESIGN_SYSTEM (read at
  `/home/kondr/KalBacktester/.claude/agents/` (moved there from `design/`)).

## Done when
- `PYTHONPATH=. uv run python tests/ui/check_runs.py` exits 0 with only `[PASS]` lines.
- `uvx ruff check ui/ tests/ui/` is clean.
- By hand (the lead): `uv run python -m ui.serve --port 8765` and open
  `http://127.0.0.1:8765/runs` against live `kalbt` (read-only; run 31 is there).

Corrected after QA round 1: null byte in a static name and over-long run ids give 404, not 500.

If anything here is unclear or looks wrong: stop and report, do not guess.
