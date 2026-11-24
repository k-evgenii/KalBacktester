"""Screen 3: analytics of one run (metrics, equity, drawdown, daily P&L, by city, by event, trades)."""
from decimal import Decimal

from portfolio.marks import equity_curve
from portfolio.metrics import daily_points, event_risk, metrics, trades
from ui.layout import MISSING, chip, count, esc, money, page, table, td
from ui.portfolio import pnl, tile


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


def drawdown_series(curve):
    """Equity minus its running peak (0 or negative) for every curve row."""
    peak = curve[0]["equity"]
    series = []
    for row in curve:
        peak = max(peak, row["equity"])
        series.append(row["equity"] - peak)
    return series


def panel_message(top, message, word):
    return top + f'<section class="panel"><p>{message} {chip(word, "mute")}</p></section>'


def analytics_page(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM run WHERE run_id = %s", (run_id,))
    if cur.fetchone() is None:
        return None
    title = f"Analytics · run {run_id}"
    top = (
        f'<p><a href="/runs/{run_id}">← Run {run_id}</a> · <a href="/runs/{run_id}/portfolio">Portfolio</a></p>'
        f'<h1>Analytics · <span class="mono">run {run_id}</span></h1>'
    )
    cur.execute("SELECT to_regclass('portfolio_ledger') IS NULL")
    if cur.fetchone()[0]:
        body = panel_message(top, "Portfolio views are not applied to this database yet.", "Not available")
        return page(title, body, "runs")

    curve, trade_rows, event_rows, m = analytics_data(conn, run_id)
    if not curve:
        return page(title, panel_message(top, "This run has no fills.", "No fills"), "runs")

    daily = daily_points(curve)
    sections = [
        top,
        tiles_section(curve, m),
        equity_section(run_id, curve, m),
        drawdown_section(run_id, curve, m),
        daily_section(daily),
        city_section(trade_rows),
        event_section(event_rows, trade_rows),
        trades_section(trade_rows),
    ]
    return page(title, "".join(sections), "runs")


def show(value, fmt):
    """fmt(value), or MISSING when the metric has no value."""
    if value is None:
        return MISSING
    return fmt(value)


def tiles_section(curve, m):
    trades_text = count(m["n_trades"])
    if m["n_open"] > 0:
        trades_text += f" · {m['n_open']} open"
    drawdown = money(m["max_drawdown"]) + f" · {m['max_drawdown_pct'] * 100:.1f} %"
    var = f"{money(m['var_95'])} / {money(m['var_99'])}"
    tiles = (
        tile("Net P&amp;L", pnl(m["total_pnl"]), answer=True)
        + tile("Return", show(m["total_return"], lambda v: f"{v * 100:+.2f} %"))
        + tile("Sharpe (daily, ann.)", show(m["sharpe_daily"], lambda v: f"{v:.2f}"))
        + tile("Max drawdown", drawdown)
        + tile("VaR 95 / 99 (daily)", var)
        + tile("Win rate", show(m["win_rate"], lambda v: f"{v * 100:.1f} %"))
        + tile("Profit factor", show(m["profit_factor"], lambda v: f"{v:.2f}"))
        + tile("Expectancy / trade", pnl(m["expectancy"]))
        + tile("Trades", trades_text)
        + tile("Fees", money(m["fees"]))
        + tile("Time in market", show(m["time_in_market"], lambda v: f"{v * 100:.0f} %"))
        + tile("Peak capital at risk", money(m["peak_capital_at_risk"]))
        + tile("Worst event loss", money(m["max_event_worst_loss"]))
    )
    summary = (
        f"starting cash {money(m['starting_cash'])} · {curve[0]['ts']:%Y-%m-%d} – "
        f"{curve[-1]['ts']:%Y-%m-%d} · {count(m['n_days'])} days"
    )
    return f'<section class="tiles">{tiles}</section><p class="mono">{summary}</p>'


def equity_section(run_id, curve, m):
    chart = line_chart([r["ts"] for r in curve], [r["equity"] for r in curve],
                       m["starting_cash"], f"Equity of run {run_id}, hourly")
    note = (f"Equity marked hourly: long YES at the bid, long NO at 1 − ask. "
            f"Ended at {money(m['final_equity'])}.")
    return f'<section class="panel"><h2>Equity</h2>{chart}<p>{note}</p></section>'


def drawdown_section(run_id, curve, m):
    chart = line_chart([r["ts"] for r in curve], drawdown_series(curve), Decimal(0),
                       f"Drawdown of run {run_id}, hourly")
    if m["max_drawdown_at"] is None:
        note = "No drawdown."
    else:
        note = (f"Largest drawdown {money(m['max_drawdown'])} ({m['max_drawdown_pct'] * 100:.1f} %) at "
                f'<span class="mono">{m["max_drawdown_at"]:%Y-%m-%d %H:%M}</span> UTC.')
    return f'<section class="panel"><h2>Drawdown</h2>{chart}<p>{note}</p></section>'


def daily_section(daily):
    wins = sum(1 for d in daily if d["pnl"] > 0)
    losses = sum(1 for d in daily if d["pnl"] < 0)
    note = f"{len(daily)} days, {wins} up, {losses} down."
    return f'<section class="panel"><h2>P&amp;L by day</h2>{bar_chart(daily)}<p>{note}</p></section>'


def city_section(trade_rows):
    cities = {}   # underlying_id -> [trades, wins, net pnl, fees]
    for t in trade_rows:
        row = cities.setdefault(t["underlying_id"], [0, 0, Decimal(0), Decimal(0)])
        row[0] += 1
        if not t["open"] and t["pnl_net"] > 0:
            row[1] += 1
        row[2] += t["pnl_net"]
        row[3] += t["fees"]
    head = [("City", False), ("Trades", True), ("Wins", True), ("Net P&amp;L", True), ("Fees", True)]
    rows = []
    for city in sorted(cities):
        n_trades, n_wins, net, fees = cities[city]
        rows.append(
            "<tr>"
            + td(esc(city), "mono")
            + td(count(n_trades), "num")
            + td(count(n_wins), "num")
            + td(pnl(net), "num")
            + td(money(fees), "num")
            + "</tr>"
        )
    return f'<section class="panel"><h2>P&amp;L by city</h2>{table(head, rows)}</section>'


def event_section(event_rows, trade_rows):
    per_event = {}   # event_ticker -> [trades, net pnl]
    for t in trade_rows:
        row = per_event.setdefault(t["event_ticker"], [0, Decimal(0)])
        row[0] += 1
        row[1] += t["pnl_net"]
    head = [("Event date", False), ("City", False), ("Event", False), ("Trades", True),
            ("Net P&amp;L", True), ("Worst-case loss", True)]
    rows = []
    for e in sorted(event_rows, key=lambda r: (r["event_date"], r["event_ticker"])):
        n_trades, net = per_event[e["event_ticker"]]
        link = f'<a href="/markets/{esc(e["underlying_id"])}/{e["event_date"]}">{e["event_date"]}</a>'
        rows.append(
            "<tr>"
            + td(link, "mono")
            + td(esc(e["underlying_id"]), "mono")
            + td(esc(e["event_ticker"]), "mono")
            + td(count(n_trades), "num")
            + td(pnl(net), "num")
            + td(money(e["max_worst_loss"]), "num")
            + "</tr>"
        )
    return f'<section class="panel"><h2>P&amp;L by event</h2>{table(head, rows)}</section>'


def trades_section(trade_rows):
    head = [("First fill", False), ("Market", False), ("City", False), ("Event date", False),
            ("Fills", True), ("Fees", True), ("Net P&amp;L", True), ("Status", False)]
    rows = []
    for t in trade_rows:
        status = chip("Open", "info") if t["open"] else chip("Closed", "mute")
        rows.append(
            "<tr>"
            + td(f"{t['first_ts']:%Y-%m-%d %H:%M}", "mono")
            + td(esc(t["market_ticker"]), "mono")
            + td(esc(t["underlying_id"]), "mono")
            + td(f"{t['event_date']}", "mono")
            + td(count(t["n_fills"]), "num")
            + td(money(t["fees"]), "num")
            + td(pnl(t["pnl_net"]), "num")
            + td(status)
            + "</tr>"
        )
    return f'<section class="panel"><h2>Trades</h2>{table(head, rows)}</section>'
