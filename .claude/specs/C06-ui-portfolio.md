# C06 — UI screen 2: the portfolio of a run, day by day

Track: C (UI lead). Second UI unit of the brief `.claude/specs/C04-ui-brief.md`.
STATUS: approved (overnight playbook: specs inside the queue count as user-approved).

## Goal
A read-only page per run showing its book day by day from the C01–C03 views: four KPI tiles,
a small line of net P&L by day, the daily book table, the open positions per day and the
breakdown per underlying. Reached from the run page of C05.

## Not built
No marks, equity or unrealized P&L (no market values exist in the views yet), no capital at
risk, no filters, no HTMX, no download. Nothing is written to the database.

## Files
- `ui/portfolio.py` (new): the page
- `ui/serve.py` (edit: one new route, nothing else)
- `ui/runs.py` (edit: a link from the run page's "Portfolio check" section heading to the new
  page, nothing else)
- `ui/static/app.css` (edit: append rules for tiles, the chart and P&L colours; nothing else)

## Interfaces

Route: `/runs/<run_id>/portfolio` (run id 1–18 digits, same rule as C05) →
`portfolio.portfolio_page(conn, run_id)`; `None` → 404 page.

```python
# ui/portfolio.py
def portfolio_page(conn, run_id: int) -> str | None
def pnl(value) -> str          # Decimal | None
def pnl_chart(days: list[date], values: list[Decimal]) -> str   # inline <svg>
```

- `portfolio_page` returns `None` when the run is not in `run`. When the view
  `portfolio_daily` does not exist (`to_regclass('portfolio_daily') IS NULL`) it returns a
  page whose body says "Portfolio views are not applied to this database yet." with a `mute`
  chip `Not available`.
- `pnl`: a sign on every non-zero value, 2 decimals, thousands separator, wrapped in a span:
  `<span class="pnl pnl-pos">+45.48</span>`, `<span class="pnl pnl-neg">-2.62</span>`,
  `<span class="pnl pnl-zero">0.00</span>`; `None` → the missing mark from `layout`.
- Page title "Portfolio · run <id>"; nav active item "runs"; a back link to `/runs/<id>`.

Sections, in this order (all numbers right-aligned; money with `layout.money`, counts with
`layout.count`, prices with `layout.price`, days as `YYYY-MM-DD` in mono):

1. **Tiles** (from the last row of `portfolio_daily` for the run, by `day`): "Cash"
   (`cash_end`, money), "Open positions cost" (`cost_basis_open`, money), "Net P&L after fees"
   (`net_pnl_cum`, `pnl`; this is the answer tile, its figure 32px, the others 22px), "Fees"
   (`fees_cum`, money). Under the tiles one mono line: `starting cash <money> · <first day> –
   <last day> · <n> days`.
2. **Chart**: `pnl_chart` of `net_pnl_cum` by day, then one sentence:
   "Net P&L after fees ended at <pnl> on <last day>."
3. **Daily book**: one row per `portfolio_daily` row, oldest first. Columns: Day, Premium paid,
   Premium received, Fees, Payouts, Cash, Open cost, Realized P&L (`pnl`), Fees to date,
   Net P&L (`pnl`), Open markets (count).
4. **Positions**: one row per `portfolio_position_daily` row, ordered by day then market.
   Columns: Day, Market (mono), Side (`YES` when `net_units > 0`, else `NO`), Units
   (`abs(net_units)`, count), Avg cost (price), Cost (money). No rows → "No open positions."
5. **By underlying** (last day only, from `portfolio_daily_underlying`): rows for underlyings
   where any of `cost_basis_open, realized_pnl_cum, fees_cum, open_markets` is non-zero, ordered
   by `underlying_id`. Columns: Underlying (mono), Open cost, Realized P&L (`pnl`), Fees,
   Net P&L (`pnl`), Open markets. Below the table, when other underlyings exist:
   "<n> other underlyings: nothing held or traded (all 0)." (`1 other underlying` for one).

A run with no ledger events still has calendar days (C02): the tiles show starting cash,
zeros, the chart is drawn flat at 0, positions say "No open positions.", and "By underlying"
says "<n> other underlyings: nothing held or traded (all 0)." with no table.

Every value from the database is escaped.

## Hard parts

**pnl_chart** — use as written:

```python
def pnl_chart(days, values):
    """Inline SVG line of values by day, with a zero line and the last value labelled."""
    width, height, pad_x, pad_y = 640, 160, 16, 16
    label_room = 96  # space right of the line for the end label
    low = min(min(values), Decimal(0))
    high = max(max(values), Decimal(0))
    if high == low:
        high = low + 1
    plot_w = width - 2 * pad_x - label_room
    plot_h = height - 2 * pad_y

    def x(i):
        return pad_x + (plot_w * i / (len(values) - 1) if len(values) > 1 else plot_w / 2)

    def y(v):
        return pad_y + plot_h * float((high - v) / (high - low))

    points = " ".join(f"{x(i):.1f},{y(v):.1f}" for i, v in enumerate(values))
    zero = y(Decimal(0))
    last_x, last_y = x(len(values) - 1), y(values[-1])
    label = esc(f"{values[-1]:+,.2f}" if values[-1] != 0 else "0.00")
    return (
        f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="Net P&amp;L after fees by day, {esc(days[0])} to {esc(days[-1])}">'
        f'<line class="chart-zero" x1="{pad_x}" y1="{zero:.1f}" x2="{pad_x + plot_w}" y2="{zero:.1f}"/>'
        f'<polyline class="chart-line" points="{points}"/>'
        f'<circle class="chart-end" cx="{last_x:.1f}" cy="{last_y:.1f}" r="3"/>'
        f'<text class="chart-label" x="{last_x + 8:.1f}" y="{last_y + 4:.1f}">{label}</text>'
        "</svg>"
    )
```

CSS to append (the rest of the new rules — tile grid `repeat(auto-fit, minmax(190px, 1fr))`
gap 12px, tile = card, tile label 12px `--muted`, figure 22px / answer 32px 700 tabular — you
write, following C05's style):

```css
.pnl { font-variant-numeric: tabular-nums; }
.pnl-pos { color: var(--ok-fg); }
.pnl-neg { color: var(--red-fg); }
.pnl-zero { color: var(--muted); }
.chart { width: 100%; max-width: 640px; height: auto; display: block; }
.chart-zero { stroke: var(--divider); stroke-width: 1; }
.chart-line { fill: none; stroke: var(--accent); stroke-width: 2; }
.chart-end { fill: var(--accent); }
.chart-label { fill: var(--text); font: 12px var(--mono); }
```

## Agreed tests

`tests/ui/check_portfolio_page.py`, house style, `PYTHONPATH=.`. Throwaway database
`kalbt_ui_portfolio_test` (dropped first and at the end): `db/schema.sql`, `db/portfolio.sql`,
recon fixture and `clean_run` by import from `tests.recon.check_check_run`. Server via
`ui.serve.make_server(0, db)` in a daemon thread. Never write to or run DDL on live `kalbt`.
Expected numbers are read from the views in the same database (the page must show exactly
what the views hold), plus the hand values below.

1. `GET /runs/<clean run>/portfolio` → 200; the four tiles show the last `portfolio_daily`
   row's `cash_end`, `cost_basis_open`, `net_pnl_cum` (with sign and class) and `fees_cum`,
   formatted as specified.
2. The daily book has one row per `portfolio_daily` row of the run, in day order, and each
   row's Cash cell equals that row's `cash_end` formatted with `money`.
3. The positions table has one row per `portfolio_position_daily` row of the run; M1 on
   2026-06-01 shows Side `YES`, Units `10`, Avg cost `0.45`.
4. By underlying: `U1` is a row and equals the last day of `portfolio_daily` for the shared
   columns. After inserting a second underlying `U2` (no series or trades), the page shows
   "1 other underlying: nothing held or traded (all 0)."
5. The chart: one `<svg class="chart"`, its polyline has as many points as `portfolio_daily`
   has rows for the run, and the sentence "Net P&L after fees ended at" names the last day.
6. `pnl(Decimal("45.48"))` = `<span class="pnl pnl-pos">+45.48</span>`;
   `pnl(Decimal("-2.62"))` contains `pnl-neg` and `-2.62`; `pnl(Decimal("0"))` contains
   `pnl-zero` and `0.00`; `pnl(Decimal("1045.5"))` contains `+1,045.50`; `pnl(None)` contains `—`.
7. `pnl_chart` with one day, and with all values equal (e.g. three zeros), returns an `<svg`
   without raising; with values `[-1, 2]` the zero line lies between the two points' y values.
8. A run with no outcomes (insert a `run` row directly with dates 2026-06-01 to 2026-06-02):
   200; Cash tile equals its starting cash; "No open positions."; the "other underlyings"
   sentence; the chart polyline has 2 points.
9. `GET /runs/999999/portfolio` → 404; `GET /runs/abc/portfolio` → 404.
10. A database with only `db/schema.sql` (+ fixture, `clean_run`): `/runs/<id>/portfolio` → 200
    with "Portfolio views are not applied".
11. The run page `/runs/<id>` contains a link `href="/runs/<id>/portfolio"`; C05's
    `tests/ui/check_runs.py` still passes unchanged.
12. Escaping: a market ticker with `<b>&"` (insert a market and one filled outcome directly in
    the throwaway database) appears escaped in the positions table.
13. Live `kalbt` row counts in `run`, `order_intent`, `order_outcome` unchanged.

## Test files
- `tests/ui/check_portfolio_page.py` (new)

## Legacy reference
- `legacy/sql/results.sql` `run_equity` (the old per-bar equity table this page replaces).
- Design rules (read-only): `/home/kondr/KalBacktester/.claude/agents/daylight-graphite.md`
  (KPI figure 30–36px and ≥1.3× its siblings; P&L coloured by good or bad; a sign on every
  change; one sentence per chart; label the line end directly).

## Done when
- `PYTHONPATH=. uv run python tests/ui/check_portfolio_page.py` exits 0, only `[PASS]` lines.
- `PYTHONPATH=. uv run python tests/ui/check_runs.py` exits 0.
- `uvx ruff check ui/ tests/ui/` clean.

If anything here is unclear or looks wrong: stop and report, do not guess.
