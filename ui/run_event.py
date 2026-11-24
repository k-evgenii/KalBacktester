"""Screen 3: one run's fills drawn on the YES bid/ask chart of every market it traded in one event."""
from datetime import UTC
from decimal import Decimal

from psycopg.rows import dict_row

from query import market as q
from ui.layout import chip, count, esc, money, page, price, table, td
from ui.markets import bracket_label, result_chip

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


def outcome_chip(order):
    if order["outcome"] == "filled":
        return chip("Filled", "ok")
    if order["outcome"] == "rejected":
        return chip(f"Rejected · {order['reason_code']}", "warn")
    return chip(f"Blocked · {order['reason_code']}", "mute")


def orders_section(orders):
    head = [("Time", False), ("Market", False), ("Side", False), ("Action", False), ("Qty", True),
            ("Outcome", False), ("Price", True), ("Fee", True)]
    rows = []
    for o in orders:
        rows.append(
            "<tr>"
            + td(f"{o['occurred_at']:%Y-%m-%d %H:%M}", "mono")
            + td(esc(o["market_ticker"]), "mono")
            + td(esc(o["side"]))
            + td(esc(o["action"]))
            + td(count(o["quantity"]), "num")
            + td(outcome_chip(o))
            + td(price(o["price"]), "num")
            + td(money(o["fee"]), "num")
            + "</tr>"
        )
    return f'<section class="panel"><h2>Orders</h2>{table(head, rows)}</section>'


def run_event_page(conn, run_id, event_ticker):
    cur = conn.cursor(row_factory=dict_row)
    cur.execute("SELECT 1 FROM run WHERE run_id = %s", (run_id,))
    if cur.fetchone() is None:
        return None
    cur.execute(ORDERS_SQL, (run_id, event_ticker))
    orders = cur.fetchall()
    cur.execute(EVENT_SQL, (event_ticker,))
    event = cur.fetchone()
    if not orders or event is None:
        return None
    for o in orders:
        o["occurred_at"] = o["occurred_at"].astimezone(UTC)

    fills = [o for o in orders if o["outcome"] == "filled"]
    quotes = q.quotes(conn, event_ticker)
    times = [r["ts"] for r in quotes] + [f["occurred_at"] for f in fills]
    start, end = min(times), max(times)
    quotes_by_market = {}
    for r in quotes:
        quotes_by_market.setdefault(r["market_ticker"], []).append(r)

    top = (
        f'<p><a href="/runs/{run_id}/analytics">← Analytics · run {run_id}</a> · '
        f'<a href="/markets/{esc(event["underlying_id"])}/{event["event_date"]}">Market day →</a></p>'
        f'<h1><span class="mono">{esc(event_ticker)}</span> · run {run_id}</h1>'
    )
    panels = []
    for market in q.ladder(conn, event_ticker):
        ticker = market["market_ticker"]
        market_fills = [f for f in fills if f["market_ticker"] == ticker]
        if not market_fills:
            continue
        chart = price_chart(ticker, quotes_by_market.get(ticker, []), market_fills, start, end)
        note = ("YES bid and ask every 15 minutes, UTC. Circles: fills at their YES-equivalent price "
                "(a NO fill at p is drawn at 1 − p).")
        panels.append(
            f'<section class="panel"><h2>{esc(bracket_label(market["temp_lo"], market["temp_hi"]))} '
            f'<span class="mono">{esc(ticker)}</span> {result_chip(market["result"])}</h2>'
            f'{chart}<p class="quote-note">{note}</p></section>'
        )
    title = f"{event_ticker} · run {run_id}"
    return page(title, top + "".join(panels) + orders_section(orders), "runs")
