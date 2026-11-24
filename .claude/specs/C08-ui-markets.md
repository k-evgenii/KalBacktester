# C08 — UI screen 3: a city's market on one day

Track: C (UI lead). Third UI unit of the brief `.claude/specs/C04-ui-brief.md`.
STATUS: approved (overnight playbook). Names fixed against Track A's A022 (merged on dev,
tag night/A022): module `query/market.py`, every function takes the connection first.

## Goal
Read-only pages to look at the markets: a list of cities, the event days of one city, and for
one city and day the bracket ladder (with result and settled high) plus the quote history of
every bracket as small multiples on one shared price axis.

## Not built
No weather on these pages (screen 4, C09), no coverage (screen 5), no HTMX, no filters beyond
the links, no trades, no order book, nothing written to the database.

## Files
- `ui/markets.py` (new): the three pages and the small-multiples SVG
- `ui/serve.py` (edit: three routes, nothing else)
- `ui/layout.py` (edit: a second nav link "Markets" → `/markets`, active when
  `active == "markets"`; nothing else)
- `ui/static/app.css` (edit: append rules for the small multiples)

## Interfaces

Data comes only from Track A's `query.market` (`from query import market as q`); each returns
a list of dicts, read-only, no commits:
- `q.cities(conn)` → `underlying_id, description, station_id, series_ticker` (only underlyings
  with a station and a series; ordered by series ticker)
- `q.event_days(conn, underlying_id)` → `event_date, event_ticker, n_markets, settled`
  (oldest first)
- `q.ladder(conn, event_ticker)` → `market_ticker, ladder_pos, temp_lo, temp_hi, category, result, settled_high_f`
- `q.quotes(conn, event_ticker, step_minutes=15)` → `market_ticker, ts, yes_bid, yes_ask, volume`
  (bid and ask from the same candle; NULL = no quote yet; volume over (ts − step, ts])

Routes (GET; city ids match `[A-Z0-9_]{1,40}` (real ids look like `NYC_HIGH`), dates `\d{4}-\d{2}-\d{2}`; anything else → 404):
- `/markets` → `markets.cities_page(conn)`
- `/markets/<underlying_id>` → `markets.days_page(conn, underlying_id)`; a city not in
  `q.cities(conn)` → 404
- `/markets/<underlying_id>/<event_date>` → `markets.day_page(conn, underlying_id, event_date)`;
  no event that day → 404

```python
# ui/markets.py
def cities_page(conn) -> str
def days_page(conn, underlying_id: str) -> str | None
def day_page(conn, underlying_id: str, event_date: date) -> str | None
def bracket_label(temp_lo, temp_hi) -> str
def quote_panel(market_ticker: str, label: str, rows: list[dict], start, end, settled_yes: bool) -> str
```

**Cities** (`/markets`): one table, in the order `q.cities` returns: City (link, mono id),
Description, Station (mono), Series (mono).

**Days** (`/markets/<city>`): title "<city> · markets"; one table, newest day first (reverse of `q.event_days`): Day
(link, mono), Event (mono), Markets (count), Status chip (`settled` → `Settled` ok, else
`Open` info). No days → "No events loaded for this city."

**Day** (`/markets/<city>/<date>`): title "<city> · <date>"; back link to the days page.
1. Heading line: event ticker (mono); settled high `<n> °F` or the missing mark; status chip.
2. **Ladder** table, ordered by `ladder_pos`: Pos (count), Bracket (`bracket_label`),
   Category, Result chip (`yes` → `YES` ok; `no` → `No` mute; null → `Open` info), Last bid,
   Last ask (price, from that market's last `quotes` row; missing mark when null), Volume
   (count, sum of that market's `quotes.volume`). The winning row (result `yes`) gets class
   `row-win`.
3. **Quotes**: one `quote_panel` per ladder row in ladder order, in a grid
   (`repeat(auto-fit, minmax(190px, 1fr))`, gap 12px). One sentence under the grid:
   "Bid and ask every 15 minutes from <first ts> to <last ts> UTC; all panels share the 0–1
   price axis." No quotes at all → "No quotes loaded for this day." instead of the grid.

`bracket_label`: both bounds → `60–61 °F`; only `temp_hi` → `≤ 59 °F`; only `temp_lo` →
`≥ 70 °F`; neither → the plain text `no range` (it is also used inside the SVG's
`aria-label`, so it must be plain text, not markup).

## Hard parts

**quote_panel** — use as written (time on x from `start` to `end`, price 0–1 on y, ask and bid
as two lines; a gap where a value is missing):

```python
def quote_panel(market_ticker, label, rows, start, end, settled_yes):
    """Small SVG of one bracket's yes_bid / yes_ask by time, shared 0-1 price axis."""
    width, height, pad = 200, 80, 4
    span = (end - start).total_seconds() or 1

    def x(ts):
        return pad + (width - 2 * pad) * (ts - start).total_seconds() / span

    def y(p):
        return pad + (height - 2 * pad) * float(1 - p)

    def lines(field):
        runs, run = [], []
        for r in rows:
            if r[field] is None:
                if run:
                    runs.append(run)
                run = []
            else:
                run.append(f"{x(r['ts']):.1f},{y(r[field]):.1f}")
        if run:
            runs.append(run)
        return "".join(f'<polyline class="q-{field}" points="{" ".join(p)}"/>' for p in runs)

    css = "q-panel q-panel-win" if settled_yes else "q-panel"
    return (
        f'<figure class="{css}"><figcaption>{esc(label)} '
        f'<span class="mono">{esc(market_ticker)}</span></figcaption>'
        f'<svg viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{esc(label)} bid and ask, 0 to 1">'
        f'<line class="q-mid" x1="{pad}" y1="{y(Decimal("0.5")):.1f}" '
        f'x2="{width - pad}" y2="{y(Decimal("0.5")):.1f}"/>'
        f'{lines("yes_ask")}{lines("yes_bid")}</svg></figure>'
    )
```

CSS to append (as written; the grid and figure margins you write):

```css
.q-panel { margin: 0; padding: 8px; border: 1px solid var(--border); border-radius: 10px; background: var(--surface); }
.q-panel-win { border-color: var(--ok-dot); }
.q-panel figcaption { font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.q-panel svg { width: 100%; height: auto; display: block; }
.q-yes_ask { fill: none; stroke: var(--accent); stroke-width: 1.5; }
.q-yes_bid { fill: none; stroke: var(--muted); stroke-width: 1.5; }
.q-mid { stroke: var(--divider); stroke-width: 1; }
.row-win td { font-weight: 600; }
```

## Agreed tests

`tests/ui/check_markets.py`, house style, `PYTHONPATH=.`. Throwaway database
`kalbt_ui_markets_<pid>` (the pid suffix avoids clashes with other chats running the same
suite): `db/schema.sql`, `db/portfolio.sql`, then a hand fixture: stations `KNYC` and `KMDW`, underlyings `NYC_HIGH` and `CHI_HIGH` (with
their stations), series `KXHIGHNY` and `KXHIGHCHI`, one settled NYC event on
2026-06-01 with three markets (less 60, between 60–61, greater 61; settled high 61 → the
between market wins), candles for two of them, one unsettled NYC event on 2026-06-02 with no
candles. Server via `make_server(0, db)`.

1. `/markets` lists `CHI_HIGH` and `NYC_HIGH` as links; the nav link "Markets" is `aria-current="page"`.
2. `/markets/NYC_HIGH` lists 2026-06-02 then 2026-06-01; statuses `Open` and `Settled`.
3. `/markets/NYC_HIGH/2026-06-01`: ladder rows in order `≤ 59 °F`, `60–61 °F`, `≥ 62 °F`; the middle
   row has `row-win` and `YES`; settled high `61 °F`.
4. Last bid / ask / volume in the ladder equal the last `quotes` row and the volume sum from
   the query function for each market.
5. Three quote panels; the winning one has `q-panel-win`; every polyline point lies inside
   0..200 × 0..80; a market with no candles has a panel with no polyline.
6. `/markets/NYC_HIGH/2026-06-02` shows "No quotes loaded for this day."
7. `bracket_label`: (60, 61) → `60–61 °F`; (None, 59) → `≤ 59 °F`; (62, None) → `≥ 62 °F`;
   (None, None) → `no range`.
8. 404s: `/markets/XXX`, `/markets/NYC_HIGH/2026-07-01`, `/markets/NYC_HIGH/2026-13-40`,
   `/markets/nyc;drop/2026-06-01`, `/markets/` + 41 letters.
9. Escaping of a description containing `<>&"`.
10. C05 and C06 suites (`tests/ui/check_runs.py`, `tests/ui/check_portfolio_page.py`) still pass.
11. Live `kalbt` row counts in `run`, `order_intent`, `order_outcome` unchanged.

## Test files
- `tests/ui/check_markets.py` (new)

## Legacy reference
- `debugging/kalshi_market_data/plot_kxhighny_week.ipynb` (the notebook view of the same data).
- Design (read-only): `/home/kondr/KalBacktester/.claude/agents/daylight-graphite.md` (small
  multiples share one axis range; one sentence per chart).

## Done when
- `PYTHONPATH=. uv run python tests/ui/check_markets.py` exits 0, only `[PASS]` lines.
- `tests/ui/check_runs.py` and `tests/ui/check_portfolio_page.py` still exit 0.
- `uvx ruff check ui/ tests/ui/` clean; clean-export run before READY.

Corrected after build round 1: the small-multiple classes are `q-panel` / `q-panel-win` (`.panel` is
the existing card class), and `bracket_label` returns plain text `no range` for no bounds.

If anything here is unclear or looks wrong: stop and report, do not guess.
