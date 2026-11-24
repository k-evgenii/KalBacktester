"""Screen 2: the portfolio of one run, day by day, from the portfolio_* views."""
from decimal import Decimal

from ui.layout import MISSING, chip, count, esc, money, page, price, table, td

DAILY_SQL = """
SELECT day, starting_cash, premium_paid, premium_received, fees, payouts, cash_end,
       cost_basis_open, realized_pnl_cum, fees_cum, net_pnl_cum, open_markets
FROM portfolio_daily WHERE run_id = %s ORDER BY day
"""

POSITIONS_SQL = """
SELECT day, market_ticker, net_units, avg_cost, cost_basis
FROM portfolio_position_daily WHERE run_id = %s ORDER BY day, market_ticker
"""

UNDERLYING_SQL = """
SELECT underlying_id, cost_basis_open, realized_pnl_cum, fees_cum, net_pnl_cum, open_markets
FROM portfolio_daily_underlying
WHERE run_id = %s AND day = (SELECT max(day) FROM portfolio_daily WHERE run_id = %s)
ORDER BY underlying_id
"""


def pnl(value):
    if value is None:
        return MISSING
    if value > 0:
        css = "pnl-pos"
        text = f"+{value:,.2f}"
    elif value < 0:
        css = "pnl-neg"
        text = f"-{-value:,.2f}"
    else:
        css = "pnl-zero"
        text = "0.00"
    return f'<span class="pnl {css}">{text}</span>'


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


def portfolio_page(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM run WHERE run_id = %s", (run_id,))
    if cur.fetchone() is None:
        return None
    title = f"Portfolio · run {run_id}"
    top = (
        f'<p><a href="/runs/{run_id}">← Run {run_id}</a></p>'
        f'<h1>Portfolio · <span class="mono">run {run_id}</span></h1>'
    )
    cur.execute("SELECT to_regclass('portfolio_daily') IS NULL")
    if cur.fetchone()[0]:
        body = (
            top + '<section class="panel">'
            f'<p>Portfolio views are not applied to this database yet. {chip("Not available", "mute")}</p>'
            "</section>"
        )
        return page(title, body, "runs")

    cur.execute(DAILY_SQL, (run_id,))
    days = cur.fetchall()
    cur.execute(POSITIONS_SQL, (run_id,))
    positions = cur.fetchall()
    cur.execute(UNDERLYING_SQL, (run_id, run_id))
    underlyings = cur.fetchall()
    sections = [
        top,
        tiles_section(days),
        chart_section(days),
        daily_section(days),
        positions_section(positions),
        underlying_section(underlyings),
    ]
    return page(title, "".join(sections), "runs")


def tile(label, figure, answer=False):
    css = "tile tile-answer" if answer else "tile"
    return f'<div class="{css}"><div class="tile-label">{label}</div><div class="tile-figure">{figure}</div></div>'


def tiles_section(days):
    last = days[-1]
    (last_day, starting_cash, _paid, _received, _fees, _payouts, cash_end,
     cost_open, _realized, fees_cum, net_pnl, _open_markets) = last
    tiles = (
        tile("Cash", money(cash_end))
        + tile("Open positions cost", money(cost_open))
        + tile("Net P&amp;L after fees", pnl(net_pnl), answer=True)
        + tile("Fees", money(fees_cum))
    )
    summary = (
        f"starting cash {money(starting_cash)} · {esc(days[0][0])} – {esc(last_day)} · "
        f"{count(len(days))} days"
    )
    return f'<section class="tiles">{tiles}</section><p class="mono">{summary}</p>'


def chart_section(days):
    day_list = [row[0] for row in days]
    values = [row[10] for row in days]
    sentence = f"Net P&amp;L after fees ended at {pnl(values[-1])} on {esc(day_list[-1])}."
    return f'<section class="panel"><h2>Net P&amp;L by day</h2>{pnl_chart(day_list, values)}<p>{sentence}</p></section>'


def daily_section(days):
    head = [("Day", False), ("Premium paid", True), ("Premium received", True), ("Fees", True),
            ("Payouts", True), ("Cash", True), ("Open cost", True), ("Realized P&amp;L", True),
            ("Fees to date", True), ("Net P&amp;L", True), ("Open markets", True)]
    rows = []
    for (day, _start, paid, received, fees, payouts, cash_end,
         cost_open, realized, fees_cum, net_pnl, open_markets) in days:
        rows.append(
            "<tr>"
            + td(esc(day), "mono")
            + td(money(paid), "num")
            + td(money(received), "num")
            + td(money(fees), "num")
            + td(money(payouts), "num")
            + td(money(cash_end), "num")
            + td(money(cost_open), "num")
            + td(pnl(realized), "num")
            + td(money(fees_cum), "num")
            + td(pnl(net_pnl), "num")
            + td(count(open_markets), "num")
            + "</tr>"
        )
    return f'<section class="panel"><h2>Daily book</h2>{table(head, rows)}</section>'


def positions_section(positions):
    if not positions:
        return '<section class="panel"><h2>Positions</h2><p>No open positions.</p></section>'
    head = [("Day", False), ("Market", False), ("Side", False), ("Units", True),
            ("Avg cost", True), ("Cost", True)]
    rows = []
    for day, market, net_units, avg_cost, cost_basis in positions:
        rows.append(
            "<tr>"
            + td(esc(day), "mono")
            + td(esc(market), "mono")
            + td("YES" if net_units > 0 else "NO")
            + td(count(abs(net_units)), "num")
            + td(price(avg_cost), "num")
            + td(money(cost_basis), "num")
            + "</tr>"
        )
    return f'<section class="panel"><h2>Positions</h2>{table(head, rows)}</section>'


def underlying_section(underlyings):
    held = [row for row in underlyings if any(row[1:4]) or row[5]]
    others = len(underlyings) - len(held)
    parts = ['<section class="panel"><h2>By underlying</h2>']
    if held:
        head = [("Underlying", False), ("Open cost", True), ("Realized P&amp;L", True),
                ("Fees", True), ("Net P&amp;L", True), ("Open markets", True)]
        rows = []
        for underlying_id, cost_open, realized, fees, net_pnl, open_markets in held:
            rows.append(
                "<tr>"
                + td(esc(underlying_id), "mono")
                + td(money(cost_open), "num")
                + td(pnl(realized), "num")
                + td(money(fees), "num")
                + td(pnl(net_pnl), "num")
                + td(count(open_markets), "num")
                + "</tr>"
            )
        parts.append(table(head, rows))
    if others:
        noun = "other underlying" if others == 1 else "other underlyings"
        parts.append(f"<p>{others} {noun}: nothing held or traded (all 0).</p>")
    parts.append("</section>")
    return "".join(parts)
