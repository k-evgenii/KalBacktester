"""Screen 3: the cities, the event days of a city, and one city's market on one day."""
from decimal import Decimal

from query import market as q
from ui.layout import MISSING, chip, count, esc, page, price, table, td


def status_chip(settled):
    return chip("Settled", "ok") if settled else chip("Open", "info")


def result_chip(result):
    if result == "yes":
        return chip("YES", "ok")
    if result == "no":
        return chip("No", "mute")
    return chip("Open", "info")


def bracket_label(temp_lo, temp_hi):
    if temp_lo is not None and temp_hi is not None:
        return f"{temp_lo}–{temp_hi} °F"
    if temp_hi is not None:
        return f"≤ {temp_hi} °F"
    if temp_lo is not None:
        return f"≥ {temp_lo} °F"
    return "no range"


def cities_page(conn):
    rows = []
    for city in q.cities(conn):
        link = f'<a href="/markets/{esc(city["underlying_id"])}">{esc(city["underlying_id"])}</a>'
        rows.append(
            "<tr>"
            + td(link, "mono")
            + td(esc(city["description"]))
            + td(esc(city["station_id"]), "mono")
            + td(esc(city["series_ticker"]), "mono")
            + "</tr>"
        )
    head = [("City", False), ("Description", False), ("Station", False), ("Series", False)]
    body = f'<h1>Markets</h1><div class="panel">{table(head, rows)}</div>'
    return page("Markets", body, "markets")


def days_page(conn, underlying_id):
    if underlying_id not in [city["underlying_id"] for city in q.cities(conn)]:
        return None
    title = f"{underlying_id} · markets"
    days = list(reversed(q.event_days(conn, underlying_id)))
    if not days:
        body = f'<h1>{esc(title)}</h1><p class="panel">No events loaded for this city.</p>'
        return page(title, body, "markets")
    rows = []
    for day in days:
        link = f'<a href="/markets/{esc(underlying_id)}/{day["event_date"]}">{day["event_date"]}</a>'
        rows.append(
            "<tr>"
            + td(link, "mono")
            + td(esc(day["event_ticker"]), "mono")
            + td(count(day["n_markets"]), "num")
            + td(status_chip(day["settled"]))
            + "</tr>"
        )
    head = [("Day", False), ("Event", False), ("Markets", True), ("Status", False)]
    body = f'<h1>{esc(title)}</h1><div class="panel">{table(head, rows)}</div>'
    return page(title, body, "markets")


def day_page(conn, underlying_id, event_date):
    days = q.event_days(conn, underlying_id)
    event = next((day for day in days if day["event_date"] == event_date), None)
    if event is None:
        return None
    event_ticker = event["event_ticker"]
    ladder = q.ladder(conn, event_ticker)
    quotes = q.quotes(conn, event_ticker)
    title = f"{underlying_id} · {event_date}"
    body = (
        f'<p><a href="/markets/{esc(underlying_id)}">← {esc(underlying_id)} · markets</a></p>'
        f"<h1>{esc(title)}</h1>"
        + heading_line(event, ladder)
        + ladder_section(ladder, quotes)
        + quotes_section(ladder, quotes)
    )
    return page(title, body, "markets")


def heading_line(event, ladder):
    high = ladder[0]["settled_high_f"] if ladder else None
    high_text = MISSING if high is None else f'<span class="mono">{esc(high)} °F</span>'
    return (
        f'<p><span class="mono">{esc(event["event_ticker"])}</span> · settled high {high_text} '
        f'{status_chip(event["settled"])}</p>'
    )


def quotes_by_market(quotes):
    by_market = {}
    for row in quotes:
        by_market.setdefault(row["market_ticker"], []).append(row)
    return by_market


def ladder_section(ladder, quotes):
    by_market = quotes_by_market(quotes)
    head = [("Pos", True), ("Bracket", False), ("Category", False), ("Result", False),
            ("Last bid", True), ("Last ask", True), ("Volume", True)]
    rows = []
    for market in ladder:
        market_rows = by_market.get(market["market_ticker"], [])
        last_bid = market_rows[-1]["yes_bid"] if market_rows else None
        last_ask = market_rows[-1]["yes_ask"] if market_rows else None
        volume = sum(row["volume"] or 0 for row in market_rows)
        css = ' class="row-win"' if market["result"] == "yes" else ""
        rows.append(
            f"<tr{css}>"
            + td(count(market["ladder_pos"]), "num")
            + td(bracket_label(market["temp_lo"], market["temp_hi"]))
            + td(esc(market["category"]))
            + td(result_chip(market["result"]))
            + td(price(last_bid), "num")
            + td(price(last_ask), "num")
            + td(count(volume), "num")
            + "</tr>"
        )
    return f'<section class="panel"><h2>Ladder</h2>{table(head, rows)}</section>'


def quotes_section(ladder, quotes):
    if not quotes:
        return '<section class="panel"><h2>Quotes</h2><p>No quotes loaded for this day.</p></section>'
    by_market = quotes_by_market(quotes)
    start = min(row["ts"] for row in quotes)
    end = max(row["ts"] for row in quotes)
    panels = "".join(
        quote_panel(market["market_ticker"], bracket_label(market["temp_lo"], market["temp_hi"]),
                    by_market.get(market["market_ticker"], []), start, end, market["result"] == "yes")
        for market in ladder
    )
    note = (
        f"Bid and ask every 15 minutes from {start:%Y-%m-%d %H:%M} to {end:%Y-%m-%d %H:%M} UTC; "
        "all panels share the 0–1 price axis."
    )
    return (
        f'<section><h2>Quotes</h2><div class="quote-grid">{panels}</div>'
        f'<p class="quote-note">{note}</p></section>'
    )


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
