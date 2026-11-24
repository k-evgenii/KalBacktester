# C14 — UI screen: a run's fills on the market price chart, one event at a time

Track: C accounting (UI). Status: APPROVED (user, 2026-11-24, agreed in chat: "the trades overlaid on the
market's price chart"; user away, lead proceeds with the agreed plan).

## Goal
A read-only page `/runs/<id>/events/<event_ticker>` that draws, for every market the run traded in that
event, the YES bid and ask over time with the run's fills marked on it, followed by the run's order
outcomes in that event. The analytics page (C13) links each event to it.

## Not built
- No zoom, no JavaScript, no volume bars, no markers for rejected or blocked intents (they are in the
  table), no charts for markets the run did not trade (the market day page has them).

## Files
- `ui/run_event.py` (new)
- `ui/serve.py` (only: one route)
- `ui/analytics.py` (only: the Event cell of the "P&L by event" table becomes a link)
- `ui/static/app.css` (only: rules appended at the end)

## Interfaces
`run_event_page(conn, run_id, event_ticker) -> str | None`: None (→ 404) when the run does not exist
or has no order_outcome in that event.

`ui/serve.py`: `event_match = re.fullmatch(r"/runs/(\d{1,18})/events/([A-Z0-9-]{1,60})", path)`,
added to the 404 guard and dispatched like `portfolio_match`.

`ui/analytics.py`: Event cell → `<a class="mono" href="/runs/{run_id}/events/{event_ticker}">{event_ticker}</a>`.

Page, in order (helpers from `ui/layout.py`, `bracket_label`/`result_chip` from `ui/markets.py`,
`q.ladder`/`q.quotes` from `query/market.py`; active nav "runs"):
1. `<p><a href="/runs/{id}/analytics">← Analytics · run {id}</a> · <a href="/markets/{underlying_id}/{event_date}">Market day →</a></p>`
   and `<h1><span class="mono">{event_ticker}</span> · run {id}</h1>`.
2. One `<section class="panel">` per traded market, in ladder order: `<h2>{bracket label} <span class="mono">{market_ticker}</span> {result_chip}</h2>`
   then `price_chart(...)` then `<p class="quote-note">YES bid and ask every 15 minutes, UTC. Circles: fills at their YES-equivalent price (a NO fill at p is drawn at 1 − p).</p>`.
3. Panel `<h2>Orders</h2>`: table Time | Market | Side | Action | Qty | Outcome | Price | Fee, one row per
   order_outcome of the run in this event (ORDER BY occurred_at, outcome_id); Time `%Y-%m-%d %H:%M` mono,
   Market mono, Outcome `chip("Filled","ok")` / `chip("Rejected · {reason_code}","warn")` /
   `chip("Blocked · {reason_code}","mute")`, Price/Fee via `price`/`money` (MISSING when NULL).

## Hard parts (use as written)
```python
ORDERS_SQL = """
    SELECT o.occurred_at, i.market_ticker, i.side, i.action, o.quantity, o.outcome, o.price, o.fee,
           o.reason_code
    FROM order_outcome o
    JOIN order_intent i ON i.intent_id = o.intent_id
    JOIN market m ON m.ticker = i.market_ticker
    WHERE i.run_id = %s AND m.event_ticker = %s
    ORDER BY o.occurred_at, o.outcome_id
"""
EVENT_SQL = "SELECT underlying_id, event_date FROM event WHERE ticker = %s"


def price_chart(market_ticker, rows, fills, start, end):
    """YES bid/ask lines over time on a 0-1 axis with grid lines, and one circle per fill."""
    width, height, pad_x, pad_y = 960, 240, 40, 12
    span = (end - start).total_seconds() or 1

    def x(ts):
        return pad_x + (width - pad_x - 12) * (ts - start).total_seconds() / span

    def y(p):
        return pad_y + (height - 2 * pad_y) * float(1 - p)

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

    grid = "".join(
        f'<line class="pc-grid" x1="{pad_x}" y1="{y(g):.1f}" x2="{width - 12}" y2="{y(g):.1f}"/>'
        f'<text class="pc-axis" x="4" y="{y(g) + 4:.1f}">{g:.2f}</text>'
        for g in (Decimal("0.25"), Decimal("0.5"), Decimal("0.75"))
    )
    marks = []
    for f in fills:
        yes_price = f["price"] if f["side"] == "yes" else 1 - f["price"]
        css = "fill-buy" if f["action"] == "buy" else "fill-sell"
        title = f'{f["occurred_at"]:%Y-%m-%d %H:%M} {f["action"]} {f["side"].upper()} {count(f["quantity"])} @ {f["price"]}'
        marks.append(f'<circle class="{css}" cx="{x(f["occurred_at"]):.1f}" cy="{y(yes_price):.1f}" r="5">'
                     f"<title>{esc(title)}</title></circle>")
    return (
        f'<svg class="chart chart-wide" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="{esc(market_ticker)} YES bid and ask with fills">'
        f'{grid}{lines("yes_ask")}{lines("yes_bid")}{"".join(marks)}</svg>'
    )
```
Chart window: `start`/`end` = min/max `ts` of `q.quotes(conn, event_ticker)` over all markets, widened to
include every fill time. Fills passed to a market's chart: its rows with outcome 'filled'.
Timestamps are UTC (convert with `.astimezone(UTC)` before formatting).
CSS appended:
```css
/* C14 fills on the price chart */
.pc-grid { stroke: var(--divider); stroke-width: 1; }
.pc-axis { fill: var(--muted); font: 11px var(--mono); }
.fill-buy { fill: var(--ok-dot); stroke: var(--surface); stroke-width: 1.5; }
.fill-sell { fill: var(--red-dot); stroke: var(--surface); stroke-width: 1.5; }
```

## Agreed tests (`tests/ui/check_run_event.py`, qa-tester)
Throwaway database `kalbt_ui_run_event_test_<pid>` with the same fixture and hand run as
`tests/ui/check_analytics.py` (M1 YES 5 @ 0.45 at 2026-05-31 16:01; M2 NO 10 @ 0.55 at 2026-06-01 02:01;
both settled), served with `make_server`, dropped first and at the end. Plus one blocked outcome added to
the hand run: intent buy YES `T-M3` 2 decided 2026-06-01 12:00, outcome blocked, reason_code `locked`,
occurred 2026-06-01 12:01.
1. `GET /runs/<id>/events/EV-0601` → 200; h1 contains `EV-0601` and `run <id>`.
2. Exactly three price charts (aria-labels for `T-M1`, `T-M2` and `T-M3`? — no: `T-M3` had no fill but
   the run traded it only by a blocked intent; rule: a chart per market with at least one **filled**
   outcome) → exactly two: `T-M1 YES bid and ask with fills`, `T-M2 YES bid and ask with fills`.
3. `T-M1` chart has one `circle class="fill-buy"` with title `2026-05-31 16:01 buy YES 5 @ 0.45`;
   `T-M2` chart has one with title `2026-06-01 02:01 buy NO 10 @ 0.55`, and its `cy` equals the `cy` of
   YES price 0.45 in that chart (y(0.45) = 12 + 216 × 0.55 = 130.8).
4. Orders table: 3 rows in time order; the `T-M3` row shows `Blocked · locked`, price and fee `—`.
5. Unknown run → 404; known run with no outcome in event `EV-0601` (a hand run with no entries) → 404;
   event ticker with a lowercase letter → 404 (route does not match).
6. Analytics page of the run links `href="/runs/<id>/events/EV-0601"`.
7. Live, read-only: the first event of run 32 (from its order_outcome) → 200 in under 5 s with at least
   one `fill-buy` circle; live row counts of run, order_intent, order_outcome unchanged.
8. `tests/ui/check_analytics.py` still passes unchanged.

(Test 2's wording: the rule is "one chart per market with at least one filled outcome"; expected: two.)

## Test files
- `tests/ui/check_run_event.py`

## Legacy reference
`legacy/backtester.html` (fills drawn on the price chart) for content only.

## Done when
- `PYTHONPATH=. uv run python tests/ui/check_run_event.py` exits 0
- lead runs `uvx ruff check ui/ tests/ui/check_run_event.py` and looks at the page

If anything here is unclear or looks wrong: stop and report, do not guess.
