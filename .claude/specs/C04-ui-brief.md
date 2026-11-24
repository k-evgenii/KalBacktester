# C04 — UI brief

Track: C (UI lead). A brief for the UI units C05 onwards, not a build spec. It goes to Tracks
A and B as INFO. Design source: `/home/kondr/KalBacktester/design/`, style **Daylight /
Graphite, Operate mode** (`daylight-graphite.md`, `veklich-design.md` § BO4_DESIGN_SYSTEM).

## What the UI is for
One local, read-only screen set to look at what is in the database: the markets and the
weather of a city and day, how complete the data is, what each backtest run did, and the
portfolio of a run day by day. It changes nothing: no buttons that write, no forms that submit.

## Stack (simplest that works)
- **Server:** one Python module per screen under `ui/`, served by the standard library
  (`http.server.ThreadingHTTPServer`, `html.escape`), bound to `127.0.0.1` only. No web
  framework, no template engine: each screen is a function that returns an HTML string.
  If templates become unreadable, Jinja2 is the one dependency to ask Track A for.
- **Database:** `db.connect.connect()` with `SET default_transaction_read_only = on` on every
  connection. Screens read views and Track A's `query/` functions only, never raw journal
  joins of their own when a view exists.
- **Browser:** HTMX (one vendored file, `ui/static/htmx.min.js`) for swapping one fragment
  (filters, tabs). No other JavaScript except the theme toggle. Charts are inline SVG drawn
  on the server.
- **Fonts:** IBM Plex Sans and Mono, self-hosted under `ui/static/fonts/` with their OFL
  licence. No third-party requests from the page.
- **Run:** `uv run python -m ui.serve [--port 8765] [--db kalbt]`.

## Look (rules from the design system that matter here)
- Colour tokens from BO4 as CSS custom properties, light (Daylight) and dark (Graphite);
  default follows the OS, a header toggle overrides it.
- Dense Operate layout: content max 1360px, spacing 4–28px, cards 16–24px padding,
  radius 12 / 8, tile grid `minmax(190px, 1fr)`.
- Tables: numbers right-aligned in tabular figures, money 2 decimals, counts 0, a sign on
  every change, grey sentence-case 12px headers, no zebra, row pitch 36–48px. A missing value
  is a grey dash; a real zero is printed.
- Mono only for tickers, run ids, times and paths. Status is a chip with a dot **and a word**
  (OK, Check, RED, Waiting), never colour alone.
- P&L colour by good or bad (profit green, loss red, zero grey).
- No gradients, glows, emoji, pie charts or animation beyond toasts. Errors stay inside their
  panel with the last good time and a Retry link.

## Screens, in build order
| # | Screen | Shows | Reads | Ready? |
|---|---|---|---|---|
| 1 | **Runs** | list of runs (id, strategy, dates, status, fills, recon result, portfolio check result); a run page with the order journal (intent → outcome, price, fee, reason) | `run`, `order_intent`, `order_outcome`; `recon.check_run`, `portfolio.check_run` | yes |
| 2 | **Portfolio** | for one run: KPI tiles (cash, cost of open positions, net P&L after fees, fees), the day table (`portfolio_daily`), positions per day, per-underlying table; a small cash/P&L line by day | C01–C03 views | after `db/portfolio.sql` is on live (Track A migration) |
| 3 | **City / day market** | for one city and day: the bracket ladder (B011 `event_outcome`), quote history per bracket as small multiples on one price axis, settled high and result | `market_bracket`, `event_outcome`, `candle` via `query/` | after A022 |
| 4 | **Weather vs market** | observations and model forecasts by hour beside the bracket prices; the official high | weather tables via `query/` | after A022 |
| 5 | **Coverage** | per city and week: markets, bars, official highs, gate warnings, numeric mismatches; status chip per cell | A023 coverage query | after A023 |

Screens 1 and 2 are Track C's first build (C05, C06). Screens 3–5 are built by C from specs
that A and B help write (they know the data); A and B send `NEED`/`INFO`, not code, into `ui/`.

## What Track C needs from the others
- **Track A:** a migration that applies `db/portfolio.sql` (NEED sent separately); `query/`
  functions for screens 3–5 that return plain rows (Polars or tuples); one dependency
  decision if Jinja2 is ever needed. No new dependency is needed for C05.
- **Track B:** `recon.check_run(conn, run_id)` stays callable and read-only (it is today).
  It returns a dict: `verdict` (`PASS`|`FAIL`), `run_id`, `checked_at`, `checks` (13 dicts:
  `id, name, status` `pass`|`fail`, `detail`, `examples` up to 10) and `counts` (`intents,
  filled, rejected, blocked, settlements, snapshots`); raises `ValueError` for an unknown run.
  B keeps this shape and sends INFO before any change. Optional later on the run page:
  `backtest.compare_kalshi.compare_run(conn, run_id, dump_dirs)` (B015, reads raw dumps).

## Not in the UI
No strategy editing, no starting runs, no live trading, no login (local only), no mobile
templates in the first pass (the layout still reflows).
