"""Screen 4: one city's weather against the market on one day."""
from datetime import UTC
from decimal import ROUND_HALF_UP, Decimal

from query import market as q
from query import weather as w
from ui.layout import MISSING, count, esc, page, table, td
from ui.markets import bracket_label

SOURCE_NAMES = {"kalshi": "Kalshi settlement", "nws_cli": "NWS climate report"}
LEADS = range(8)


def forecast_highs(forecasts):
    """Maximum forecast temperature per model and lead day."""
    highs = {}
    for r in forecasts:
        by_lead = highs.setdefault(r["model"], {})
        if r["lead_day"] not in by_lead or r["temp_f"] > by_lead[r["lead_day"]]:
            by_lead[r["lead_day"]] = r["temp_f"]
    return highs


def temp_chart(observed, forecasts, settled_high):
    """SVG: observed temperature line, one grey line per model forecast, dashed settled high."""
    width, height, pad_l, pad_r, pad_y = 640, 200, 40, 16, 12
    points = [(r["observed_at"], r["temp_f"]) for r in observed if r["temp_f"] is not None]
    points += [(r["valid_time"], r["temp_f"]) for r in forecasts]
    times = [t for t, _ in points]
    temps = [v for _, v in points] + ([settled_high] if settled_high is not None else [])
    t0, t1 = min(times), max(times)
    low, high = min(temps) - 2, max(temps) + 2
    span = (t1 - t0).total_seconds() or 1

    def x(t):
        return pad_l + (width - pad_l - pad_r) * (t - t0).total_seconds() / span

    def y(v):
        return pad_y + (height - 2 * pad_y) * float((high - v) / (high - low))

    def line(rows, time_key, css):
        pts = " ".join(f"{x(r[time_key]):.1f},{y(r['temp_f']):.1f}"
                       for r in rows if r["temp_f"] is not None)
        return f'<polyline class="{css}" points="{pts}"/>' if pts else ""

    by_model = {}
    for r in forecasts:
        by_model.setdefault(r["model"], []).append(r)
    parts = [line(rows, "valid_time", "t-forecast") for rows in by_model.values()]
    parts.append(line(observed, "observed_at", "t-observed"))
    if settled_high is not None:
        parts.append(f'<line class="t-settled" x1="{pad_l}" y1="{y(settled_high):.1f}" '
                     f'x2="{width - pad_r}" y2="{y(settled_high):.1f}"/>')
    axis = (f'<text class="t-axis" x="4" y="{y(high - 2) + 4:.1f}">{esc(f"{high - 2:.0f}")}°</text>'
            f'<text class="t-axis" x="4" y="{y(low + 2) + 4:.1f}">{esc(f"{low + 2:.0f}")}°</text>')
    return (f'<svg class="chart" viewBox="0 0 {width} {height}" role="img" '
            f'aria-label="Temperature through the climate day">{axis}{"".join(parts)}</svg>')


def winning_bracket(ladder):
    for market in ladder:
        if market["result"] == "yes":
            return bracket_label(market["temp_lo"], market["temp_hi"])
    return "not settled"


def weather_page(conn, underlying_id, event_date):
    days = q.event_days(conn, underlying_id)
    event = next((day for day in days if day["event_date"] == event_date), None)
    if event is None:
        return None
    event_ticker = event["event_ticker"]
    ladder = q.ladder(conn, event_ticker)
    observed = w.observations(conn, event_ticker)
    forecasts = w.forecasts(conn, event_ticker)
    official = w.official(conn, event_ticker)
    settled_high = settled_high_f(official)
    title = f"{underlying_id} · {event_date} · weather"
    body = (
        f'<p><a href="/markets/{esc(underlying_id)}/{event_date}">← {esc(underlying_id)} · {event_date}</a></p>'
        f"<h1>{esc(title)}</h1>"
        + heading_line(event_ticker, settled_high, observed, ladder)
        + official_section(official)
        + chart_section(observed, forecasts, settled_high)
        + highs_section(forecasts, settled_high)
    )
    return page(title, body, "markets")


def settled_high_f(official):
    for source in ("kalshi", "nws_cli"):
        for row in official:
            if row["source"] == source and row["high_f"] is not None:
                return row["high_f"]
    return None


def heading_line(event_ticker, settled_high, observed, ladder):
    settled_text = MISSING if settled_high is None else f'<span class="mono">{count(settled_high)} °F</span>'
    observed_temps = [r["temp_f"] for r in observed if r["temp_f"] is not None]
    observed_text = (f'<span class="mono">{max(observed_temps):.1f} °F</span>'
                     if observed_temps else MISSING)
    return (
        f'<p><span class="mono">{esc(event_ticker)}</span> · Settled high {settled_text} · '
        f"Observed max {observed_text} · Winning bracket {esc(winning_bracket(ladder))}</p>"
    )


def official_section(official):
    if not official:
        return '<section class="panel"><h2>Official values</h2><p>No official values loaded.</p></section>'
    head = [("Source", False), ("High °F", True), ("Time of high", False),
            ("Low °F", True), ("Issued", False)]
    rows = []
    for row in official:
        source = SOURCE_NAMES.get(row["source"], esc(row["source"]))
        time_text = MISSING if row["high_time_lst"] is None else esc(row["high_time_lst"])
        low_text = MISSING if row["low_f"] is None else count(row["low_f"])
        high_text = MISSING if row["high_f"] is None else count(row["high_f"])
        if row["issued_at"] is None:
            issued_text = MISSING
        else:
            issued_text = f"{row['issued_at'].astimezone(UTC):%Y-%m-%d %H:%M}"
        rows.append(
            "<tr>"
            + td(source)
            + td(high_text, "num")
            + td(time_text, "mono")
            + td(low_text, "num")
            + td(issued_text, "mono")
            + "</tr>"
        )
    return f'<section class="panel"><h2>Official values</h2>{table(head, rows)}</section>'


def chart_section(observed, forecasts, settled_high):
    day_ahead = [r for r in forecasts if r["lead_day"] == 1]
    has_points = any(r["temp_f"] is not None for r in observed) or day_ahead
    if not has_points:
        return '<section class="panel"><h2>Temperature through the day</h2><p>No weather loaded for this day.</p></section>'
    note = ("Observed temperature (blue) and each model's day-ahead forecast (grey); "
            "the dashed line is the settled high.")
    return (
        f'<section class="panel"><h2>Temperature through the day</h2>'
        f'{temp_chart(observed, day_ahead, settled_high)}<p class="quote-note">{note}</p></section>'
    )


def highs_section(forecasts, settled_high):
    highs = forecast_highs(forecasts)
    head = [("Model", False)] + [(f"Lead {lead}", True) for lead in LEADS]
    rows = []
    for model in sorted(highs):
        cells = td(esc(model), "mono")
        for lead in LEADS:
            value = highs[model].get(lead)
            if value is None:
                cells += td(MISSING, "num")
                continue
            shown = value.quantize(Decimal("0.1"), rounding=ROUND_HALF_UP)
            hit = shown.quantize(Decimal(1), rounding=ROUND_HALF_UP) == settled_high
            cells += td(f"{shown:.1f}", "num hit" if hit else "num")
        rows.append(f"<tr>{cells}</tr>")
    note = "Forecast high per model and lead day (0 = latest run)"
    if settled_high is not None:
        note += f"; bold cells round to the settled high of {count(settled_high)} °F"
    return (
        f'<section class="panel"><h2>Forecast highs</h2>{table(head, rows)}'
        f'<p class="quote-note">{note}.</p></section>'
    )
