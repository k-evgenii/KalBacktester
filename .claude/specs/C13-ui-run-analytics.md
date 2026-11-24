# C13 — UI screen: analytics of one run (equity, drawdown, daily P&L, metrics, by city, by event, trades)

Track: C accounting (UI). Status: APPROVED (user, 2026-11-24, agreed in chat: extend the existing `ui/`
with a run page showing the equity curve, drawdown, P&L by city and day and a metrics panel; trades on
the price chart come next as C14; user away, lead proceeds with the agreed plan).

## Goal
A read-only page `/runs/<id>/analytics` that shows one run's headline metrics as tiles, its hourly
marked equity curve, drawdown and day-by-day P&L as inline SVG, and tables of P&L by city, by event
and by trade. It uses `portfolio/marks.py` (C11) and `portfolio/metrics.py` (C12) and writes nothing.

## Not built
- No fills on the market price chart (C14), no run comparison, no sweeps, no JavaScript charts,
  no new colours beyond the existing tokens.

## Files
- `ui/analytics.py` (new)
- `ui/serve.py` (only: one route)
- `ui/runs.py` (only: one link)
- `ui/static/app.css` (only: rules appended at the end)

## Interfaces
`analytics_page(conn, run_id) -> str | None` (None for an unknown run → 404, as `portfolio_page`).

`ui/serve.py`: `analytics_match = re.fullmatch(r"/runs/(\d{1,18})/analytics", path)`, added to the 404
guard and dispatched to `analytics.analytics_page(conn, int(...))` exactly like `portfolio_match`.

`ui/runs.py` `portfolio_section`: the heading becomes
`<h2>Portfolio check <a href="/runs/{run_id}/portfolio">Day by day →</a> <a href="/runs/{run_id}/analytics">Analytics →</a></h2>`.

Page structure, in order (use `page()`, `tile()`/`pnl()` from `ui/portfolio.py`, `money`, `count`,
`chip`, `table`, `td`, `esc`, `MISSING` from `ui/layout.py`; active nav "runs"):
1. `<p><a href="/runs/{id}">← Run {id}</a> · <a href="/runs/{id}/portfolio">Portfolio</a></p>` and
   `<h1>Analytics · <span class="mono">run {id}</span></h1>`.
2. If `to_regclass('portfolio_ledger')` is NULL: one panel "Portfolio views are not applied to this
   database yet." + `chip("Not available", "mute")`, nothing else. If the run has no ledger rows
   (`equity_curve` returns `[]`): one panel "This run has no fills." + `chip("No fills", "mute")`.
3. Tiles (`<section class="tiles">`), in this order, labels exactly:
   `Net P&amp;L` (answer tile, `pnl(total_pnl)`), `Return` (`f"{total_return*100:+.2f} %"`),
   `Sharpe (daily, ann.)` (`f"{v:.2f}"`), `Max drawdown` (`money(max_drawdown)` + `f" · {pct*100:.1f} %"`),
   `VaR 95 / 99 (daily)` (`money / money`), `Win rate` (`f"{v*100:.1f} %"`), `Profit factor`
   (`f"{v:.2f}"`), `Expectancy / trade` (`pnl`), `Trades` (`count(n_trades)` + `f" · {n_open} open"` when
   n_open > 0), `Fees` (`money`), `Time in market` (`f"{v*100:.0f} %"`), `Peak capital at risk`
   (`money`), `Worst event loss` (`money(max_event_worst_loss)`). Any None → `MISSING`.
   Under the tiles: `<p class="mono">starting cash {money} · {first ts:%Y-%m-%d} – {last ts:%Y-%m-%d} · {n_days} days</p>`.
4. Panel `<h2>Equity</h2>`: `line_chart(curve ts, equity, baseline=starting cash)`, then
   `<p>Equity marked hourly: long YES at the bid, long NO at 1 − ask. Ended at {money(final)}.</p>`.
5. Panel `<h2>Drawdown</h2>`: `line_chart(ts, equity − running peak, baseline=0)`, then
   `<p>Largest drawdown {money} ({pct} %) at <span class="mono">{ts:%Y-%m-%d %H:%M}</span> UTC.</p>`
   (or `<p>No drawdown.</p>` when max_drawdown_at is None).
6. Panel `<h2>P&amp;L by day</h2>`: `bar_chart(daily_points)`, then
   `<p>{n_days} days, {wins} up, {losses} down.</p>` (days with pnl > 0 / < 0).
7. Panel `<h2>P&amp;L by city</h2>`: table City | Trades | Wins | Net P&L | Fees, one row per
   `underlying_id` (sorted), from the closed and open trades (Wins = closed with pnl_net > 0).
8. Panel `<h2>P&amp;L by event</h2>`: table Event date | City | Event | Trades | Net P&L | Worst-case loss,
   one row per event from `event_risk` rows (ordered by event_date, then event_ticker), trade counts
   and pnl summed from trades by event_ticker. Event date cell links to
   `/markets/{underlying_id}/{event_date}`; Event cell mono.
9. Panel `<h2>Trades</h2>`: table First fill | Market | City | Event date | Fills | Fees | Net P&L |
   Status, one row per trade in `trades()` order; First fill `%Y-%m-%d %H:%M` mono, Market mono,
   Status `chip("Open", "info")` or `chip("Closed", "mute")`.
Money columns right-aligned (`table` head `is_number=True`), P&L cells via `pnl()`.

## Hard parts (use as written)
```python
def analytics_data(conn, run_id):
    curve = equity_curve(conn, run_id)
    trade_rows = trades(conn, run_id)
    event_rows, peak = event_risk(conn, run_id)
    worst = max((r["max_worst_loss"] for r in event_rows), default=Decimal(0))
    return curve, trade_rows, event_rows, metrics(curve, trade_rows, peak, worst)


def line_chart(times, values, baseline, label):
    """Inline SVG line of values over time, a dashed baseline, the last value labelled."""
    width, height, pad_x, pad_y, label_room = 960, 220, 16, 16, 96
    low = min(min(values), baseline)
    high = max(max(values), baseline)
    if high == low:
        high = low + 1
    t0, t1 = times[0], times[-1]
    span = (t1 - t0).total_seconds() or 1
    plot_w, plot_h = width - 2 * pad_x - label_room, height - 2 * pad_y

    def x(t):
        return pad_x + plot_w * (t - t0).total_seconds() / span

    def y(v):
        return pad_y + plot_h * float((high - v) / (high - low))

    points = " ".join(f"{x(t):.1f},{y(v):.1f}" for t, v in zip(times, values, strict=True))
    base = y(baseline)
    end_x, end_y = x(t1), y(values[-1])
    return (
        f'<svg class="chart chart-wide" viewBox="0 0 {width} {height}" role="img" aria-label="{esc(label)}">'
        f'<line class="chart-base" x1="{pad_x}" y1="{base:.1f}" x2="{pad_x + plot_w}" y2="{base:.1f}"/>'
        f'<polyline class="chart-line" points="{points}"/>'
        f'<circle class="chart-end" cx="{end_x:.1f}" cy="{end_y:.1f}" r="3"/>'
        f'<text class="chart-label" x="{end_x + 8:.1f}" y="{end_y + 4:.1f}">{esc(f"{values[-1]:,.2f}")}</text>'
        "</svg>"
    )


def bar_chart(daily):
    """One bar per daily point from the zero line: up days class bar-pos, down days bar-neg."""
    width, height, pad = 960, 160, 16
    pnls = [d["pnl"] for d in daily]
    top = max(max(abs(p) for p in pnls), Decimal("0.01"))
    plot_w, half = width - 2 * pad, (height - 2 * pad) / 2
    step = plot_w / len(pnls)
    zero = pad + half
    bars = []
    for i, d in enumerate(daily):
        h = half * float(abs(d["pnl"]) / top)
        css = "bar-pos" if d["pnl"] > 0 else "bar-neg" if d["pnl"] < 0 else "bar-zero"
        y_top = zero - h if d["pnl"] > 0 else zero
        bars.append(f'<rect class="{css}" x="{pad + i * step:.1f}" y="{y_top:.1f}" '
                    f'width="{max(step - 1, 1):.1f}" height="{max(h, 0.5):.1f}">'
                    f'<title>{esc(d["day"])} {esc(f"{d["pnl"]:+,.2f}")}</title></rect>')
    return (
        f'<svg class="chart chart-wide" viewBox="0 0 {width} {height}" role="img" '
        f'aria-label="P&amp;L by day, {esc(daily[0]["day"])} to {esc(daily[-1]["day"])}">'
        f'<line class="chart-base" x1="{pad}" y1="{zero:.1f}" x2="{width - pad}" y2="{zero:.1f}"/>'
        f'{"".join(bars)}</svg>'
    )
```
Labels: equity chart `f"Equity of run {run_id}, hourly"`, drawdown `f"Drawdown of run {run_id}, hourly"`.
Drawdown series: walk the curve keeping `peak = max(peak, equity)`; value = `equity - peak` (≤ 0).
CSS appended to `ui/static/app.css`:
```css
/* C13 analytics */
.chart-wide { max-width: 960px; }
.chart-base { stroke: var(--divider); stroke-width: 1; stroke-dasharray: 4 4; }
.bar-pos { fill: var(--ok-dot); }
.bar-neg { fill: var(--red-dot); }
.bar-zero { fill: var(--divider); }
```

## Agreed tests (`tests/ui/check_analytics.py`, qa-tester)
Throwaway database `kalbt_ui_analytics_test_<pid>` (schema + portfolio views + `load_fixture` from
`tests/recon/check_check_run.py`) with the same hand run as `tests/portfolio/check_marks.py` (M1 YES 5 @
0.45 fee 0.09; M2 NO 10 @ 0.55 fee 0.18; both settled; the two extra NULL-quote candles), served with
`make_server` on a free port, dropped first and at the end. Expected values by hand:
1. `GET /runs/<id>/analytics` → 200, title contains "Analytics", h1 contains "run <id>".
2. Tiles: Net P&L `+6.98`; Trades `2`; Fees `0.27`; Win rate `100.0 %`; Profit factor is `—` (no
   losses); Return `+0.70 %`; Peak capital at risk `7.75`; Worst event loss `7.75`.
3. Exactly three `<svg` charts in the page: aria-labels "Equity of run <id>, hourly",
   "Drawdown of run <id>, hourly", and one starting "P&amp;L by day"; the equity end label is
   `1,006.98`.
4. City table: one row `U1`, Trades 2, Wins 2, Net P&L `+6.98`, Fees `0.27`.
5. Event table: one row 2026-06-01, `U1`, `EV-0601`, Trades 2, `+6.98`, worst-case loss `7.75`; the
   date links to `/markets/U1/2026-06-01`.
6. Trades table: `T-M1` first fill `2026-05-31 16:01`, fees `0.09`, `+2.66`, Closed;
   `T-M2` `2026-06-01 02:01`, `0.18`, `+4.32`, Closed.
7. Unknown run id → 404; a hand run with no fills → 200 containing "This run has no fills.".
8. `GET /runs/<id>` contains `href="/runs/<id>/analytics"`.
9. Live, read-only: serve `kalbt`, `GET /runs/32/analytics` → 200 in under 5 s, contains "Equity of
   run 32"; live row counts of run, order_intent, order_outcome unchanged.
10. Existing suites still pass unchanged: `tests/ui/check_runs.py`, `tests/ui/check_portfolio_page.py`
    (their documented commands).

## Test files
- `tests/ui/check_analytics.py`

## Legacy reference
`legacy/backtester.html` (equity and drawdown panels, metrics panel) for content only; the look is
the existing BO4 Daylight/Graphite `ui/`.

## Done when
- `PYTHONPATH=. uv run python tests/ui/check_analytics.py` exits 0
- lead runs `uvx ruff check ui/ tests/ui/check_analytics.py` clean and looks at the page in the server

If anything here is unclear or looks wrong: stop and report, do not guess.
