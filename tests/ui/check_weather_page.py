"""Checks the weather page (spec C09, agreed tests 1 to 11, plus extra attacks E01 and up).

Creates two throwaway databases, kalbt_ui_weather_<pid> (schema + portfolio views + the spec's
hand fixture) and kalbt_ui_weather_<pid>_x (schema + portfolio views + odd cases for the extra
attacks), serves them with make_server on free ports, fetches pages over HTTP and drops both
databases first and at the end (also when a test fails). Live kalbt is only read: SELECT
count(*) (test 11) and one GET of a fixed past event. Test 10 (the C05, C06 and C08 suites
still pass) is run by hand as separate commands, not inside this file.
Run from the repo root: PYTHONPATH=. uv run python tests/ui/check_weather_page.py
"""
import html
import http.client
import os
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from db.connect import connect
from query import market as q
from query import weather as w
from ui.serve import make_server
from ui.weather import forecast_highs, official_section, temp_chart, winning_bracket

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = f"kalbt_ui_weather_{os.getpid()}"
XDB = DB + "_x"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
D = Decimal
MISSING_MARK = "—"
UGLY = '<>&"'
ESCAPED = "&lt;&gt;&amp;&quot;"

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_test(name, function, ctx):
    try:
        function(ctx)
    except Exception as error:  # noqa: BLE001 - a failed test is reported, the rest still run
        traceback.print_exc()
        report(name, False, f"{type(error).__name__}: {error}")
    else:
        report(name, True)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def fetch(port, path, method="GET"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", errors="replace")
    finally:
        conn.close()


def get_ok(port, path):
    status, body = fetch(port, path)
    assert status == 200, f"{path}: status {status}"
    return body


def start(dbname):
    server = make_server(0, dbname)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def text_of(cell):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", cell))).strip()


def section(body, heading):
    for found in re.findall(r"<section.*?</section>", body, flags=re.DOTALL):
        if re.search(rf"<h2>{re.escape(heading)}", found):
            return found
    raise AssertionError(f"no section {heading!r}")


def table_rows(fragment):
    """Body rows as lists of (td class, cell text)."""
    rows = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", fragment, flags=re.DOTALL):
        cells = re.findall(r'<td(?: class="([^"]*)")?>(.*?)</td>', row, flags=re.DOTALL)
        if cells:
            rows.append([(css, text_of(text)) for css, text in cells])
    return rows


def heading_text(body):
    """Text of the heading line paragraph (the one holding the event ticker)."""
    found = re.search(r'<p><span class="mono">[^<]*</span> · Settled high.*?</p>', body, flags=re.DOTALL)
    assert found, "no heading line"
    return text_of(found.group(0))


def chart_points(body):
    """Every (x, y) of the polylines in the page, by css class."""
    found = {}
    for css, attr in re.findall(r'<polyline class="([^"]*)" points="([^"]*)"', body):
        pts = [tuple(float(n) for n in p.split(",")) for p in attr.split()]
        found.setdefault(css, []).append(pts)
    return found


def all_inside(body):
    """True when every polyline point and settled line end lies inside 0..640 x 0..200."""
    coords = [p for lines in chart_points(body).values() for pts in lines for p in pts]
    for x1, y1, x2, y2 in re.findall(r'<line class="t-settled" x1="([-\d.]+)" y1="([-\d.]+)" '
                                     r'x2="([-\d.]+)" y2="([-\d.]+)"', body):
        coords += [(float(x1), float(y1)), (float(x2), float(y2))]
    assert coords, "no points at all"
    for x, y in coords:
        assert 0 <= x <= 640 and 0 <= y <= 200, (x, y)
    return True


def ts(day, hour, minute=0, month=6):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def live_counts():
    conn = connect()
    try:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in LIVE_TABLES}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def add_city(conn, underlying_id, station_id, series):
    conn.execute(
        "INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h,"
        " iem_id, iem_network) VALUES (%s, %s, 40, -74, 'America/New_York', -5, %s, 'NY_ASOS')",
        (station_id, "Station " + station_id, station_id[1:]))
    conn.execute("INSERT INTO underlying (underlying_id, description, station_id) VALUES (%s, %s, %s)",
                 (underlying_id, underlying_id + " high", station_id))
    conn.execute("INSERT INTO series (ticker, underlying_id, title) VALUES (%s, %s, 'title')",
                 (series, underlying_id))


def add_event(conn, series, underlying_id, event_date, event_ticker, results=("no", "yes", "no")):
    """Event with three markets (less 60, between 60-61, greater 61); results None = unsettled."""
    conn.execute(
        "INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
        " VALUES (%s, %s, %s, %s, 'src')", (event_ticker, series, underlying_id, event_date))
    shapes = [("less", None, 60), ("between", 60, 61), ("greater", 61, None)]
    for i, ((kind, floor, cap), result) in enumerate(zip(shapes, results or (None,) * 3, strict=True)):
        settled_at = datetime(2026, 6, 20, tzinfo=UTC) if result else None
        conn.execute(
            "INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike, open_time,"
            " close_time, result, settled_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (f"{event_ticker}-M{i}", event_ticker, kind, floor, cap, datetime(2026, 5, 1, tzinfo=UTC),
             datetime(2026, 6, 19, tzinfo=UTC), result, settled_at))


def add_official(conn, station, climate_date, source, high, issued, time_lst=None, low=None):
    conn.execute(
        "INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst, low_f,"
        " issued_at, reference) VALUES (%s,%s,%s,%s,%s,%s,%s,'ref')",
        (station, climate_date, source, high, time_lst, low, issued))


def add_obs(conn, station, when, temp):
    conn.execute("INSERT INTO observation_hourly (station_id, observed_at, temp_f, metar)"
                 " VALUES (%s,%s,%s,'METAR')", (station, when, temp))


def add_forecast(conn, station, model, lead, when, temp):
    conn.execute("INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f)"
                 " VALUES (%s,%s,%s,%s,%s)", (station, model, when, lead, temp))


def add_hourly(conn, station, model, lead, day, peak, hours=range(6, 19), peak_hour=15):
    """Hourly forecasts 06:00..18:00 UTC whose maximum is exactly peak (at peak_hour)."""
    for hour in hours:
        add_forecast(conn, station, model, lead, ts(day, hour), peak - D("0.5") * abs(hour - peak_hour))


def build_main(conn):
    add_city(conn, "NYC_HIGH", "KNYC", "KXHIGHNY")
    add_event(conn, "KXHIGHNY", "NYC_HIGH", date(2026, 6, 1), "EV-0601")
    add_official(conn, "KNYC", "2026-06-01", "kalshi", 61, ts(2, 0))
    add_official(conn, "KNYC", "2026-06-01", "nws_cli", 61, ts(2, 6, 30), time_lst="300 PM", low=54)
    for hour, temp in ((6, 55), (12, 59), (18, 61)):
        add_obs(conn, "KNYC", ts(1, hour), temp)
    add_obs(conn, "KNYC", ts(1, 20), None)
    add_hourly(conn, "KNYC", "gfs_seamless", 1, 1, D("60.6"))
    add_hourly(conn, "KNYC", "ecmwf_ifs025", 1, 1, D("62.0"))
    add_hourly(conn, "KNYC", "gfs_seamless", 0, 1, D("61.2"))
    add_hourly(conn, "KNYC", "ecmwf_ifs025", 0, 1, D("58.0"))
    add_event(conn, "KXHIGHNY", "NYC_HIGH", date(2026, 6, 2), "EV-0602", results=None)
    conn.commit()


def build_extra(conn):
    add_city(conn, "XW_HIGH", "KXW", "KXHIGHXW")
    # 06-10 forecast only: no observations, no official values, lead 1 forecast
    add_event(conn, "KXHIGHXW", "XW_HIGH", date(2026, 6, 10), "XW-0610", results=None)
    add_hourly(conn, "KXW", "gfs_seamless", 1, 10, D("65.0"))
    add_hourly(conn, "KXW", "ecmwf_ifs025", 1, 10, D("66.0"))
    # 06-11 observations all NULL, only lead 0 forecasts
    add_event(conn, "KXHIGHXW", "XW_HIGH", date(2026, 6, 11), "XW-0611", results=None)
    add_obs(conn, "KXW", ts(11, 12), None)
    add_obs(conn, "KXW", ts(11, 14), None)
    add_hourly(conn, "KXW", "gfs_seamless", 0, 11, D("70.0"))
    # 06-12 only nws_cli official
    add_event(conn, "KXHIGHXW", "XW_HIGH", date(2026, 6, 12), "XW-0612", results=None)
    add_official(conn, "KXW", "2026-06-12", "nws_cli", 62, ts(13, 6), time_lst="1 PM", low=50)
    # 06-13 rounding at x.5, kalshi 61 beats nws_cli 63
    add_event(conn, "KXHIGHXW", "XW_HIGH", date(2026, 6, 13), "XW-0613")
    add_official(conn, "KXW", "2026-06-13", "kalshi", 61, ts(14, 0))
    add_official(conn, "KXW", "2026-06-13", "nws_cli", 63, ts(14, 6), time_lst="2 PM", low=50)
    add_hourly(conn, "KXW", "gfs_seamless", 1, 13, D("60.5"))
    add_hourly(conn, "KXW", "gfs_seamless", 2, 13, D("61.5"))
    add_hourly(conn, "KXW", "gfs_seamless", 3, 13, D("61.49"))
    add_hourly(conn, "KXW", "gfs_seamless", 4, 13, D("60.49"))
    add_hourly(conn, "KXW", "gfs_seamless", 5, 13, D("61.45"))
    # 06-14 extreme temperatures, a model with an ugly name
    add_event(conn, "KXHIGHXW", "XW_HIGH", date(2026, 6, 14), "XW-0614")
    add_official(conn, "KXW", "2026-06-14", "kalshi", 61, ts(15, 0))
    add_obs(conn, "KXW", ts(14, 6), -20)
    add_obs(conn, "KXW", ts(14, 12), 115)
    add_obs(conn, "KXW", ts(14, 18), -20)
    add_forecast(conn, "KXW", UGLY, 1, ts(14, 6), 115)
    add_forecast(conn, "KXW", UGLY, 1, ts(14, 12), -20)
    add_forecast(conn, "KXW", UGLY, 1, ts(14, 18), 115)
    # 06-15 a single observation: zero time span, zero temperature span
    add_event(conn, "KXHIGHXW", "XW_HIGH", date(2026, 6, 15), "XW-0615", results=None)
    add_obs(conn, "KXW", ts(15, 12), 70)
    conn.commit()


def make_db(admin, name):
    admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {name}")
    conn = connect(name)
    conn.execute(SCHEMA.read_text())
    conn.execute(PORTFOLIO.read_text())
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

PAGE1 = "/markets/NYC_HIGH/2026-06-01/weather"
EVENT1 = "EV-0601"
MODELS = ["ecmwf_ifs025", "gfs_seamless"]


def test_01_page_and_heading(ctx):
    body = get_ok(ctx["port"], PAGE1)
    head = heading_text(body)
    assert "Settled high 61 °F" in head, head
    assert "Winning bracket 60–61 °F" in head, head
    assert EVENT1 in head
    assert "NYC_HIGH · 2026-06-01 · weather" in body
    assert '<a href="/markets/NYC_HIGH/2026-06-01">' in body, "back link"


def test_02_official_table(ctx):
    body = get_ok(ctx["port"], PAGE1)
    rows = table_rows(section(body, "Official values"))
    texts = [[t for _, t in r] for r in rows]
    assert texts == [["Kalshi settlement", "61", MISSING_MARK, MISSING_MARK, "2026-06-02 00:00"],
                     ["NWS climate report", "61", "300 PM", "54", "2026-06-02 06:30"]], texts


def test_03_chart(ctx):
    body = get_ok(ctx["port"], PAGE1)
    found = chart_points(body)
    assert len(found["t-observed"]) == 1 and len(found["t-observed"][0]) == 3, found["t-observed"]
    assert len(found["t-forecast"]) == 2, "lead 1 only: one line per model"
    assert body.count('class="t-settled"') == 1
    assert body.count('<svg class="chart"') == 1
    assert all_inside(body)


def test_04_forecast_highs_table(ctx):
    body = get_ok(ctx["port"], PAGE1)
    rows = table_rows(section(body, "Forecast highs"))
    assert [r[0][1] for r in rows] == MODELS, rows
    by_model = {r[0][1]: r[1:] for r in rows}
    assert all(len(cells) == 8 for cells in by_model.values())
    gfs, ecmwf = by_model["gfs_seamless"], by_model["ecmwf_ifs025"]
    assert gfs[1] == ("num hit", "60.6") and gfs[0] == ("num hit", "61.2"), gfs[:2]
    assert ecmwf[1] == ("num", "62.0") and ecmwf[0] == ("num", "58.0"), ecmwf[:2]
    for cells in (gfs, ecmwf):
        assert [t for _, t in cells[2:]] == [MISSING_MARK] * 6, cells
        assert all("hit" not in css for css, _ in cells[2:])
    assert "Observed max 61.0 °F" in heading_text(body)
    head = [text_of(c) for c in re.findall(r"<th[^>]*>(.*?)</th>", section(body, "Forecast highs"))]
    assert head == ["Model"] + [f"Lead {n}" for n in range(8)], head
    assert "bold cells round to the settled high of 61 °F" in body


def test_05_forecast_highs_function(ctx):
    rows = w.forecasts(ctx["conn"], EVENT1)
    assert len(rows) == 52
    assert forecast_highs(rows) == {
        "gfs_seamless": {0: D("61.2"), 1: D("60.6")},
        "ecmwf_ifs025": {0: D("58.0"), 1: D("62.0")}}
    assert forecast_highs([]) == {}


def test_06_winning_bracket(ctx):
    ladder = q.ladder(ctx["conn"], EVENT1)
    assert winning_bracket(ladder) == "60–61 °F", winning_bracket(ladder)
    assert winning_bracket(q.ladder(ctx["conn"], "EV-0602")) == "not settled"
    assert winning_bracket([]) == "not settled"
    synthetic = [{"result": "no", "temp_lo": None, "temp_hi": 59},
                 {"result": "yes", "temp_lo": 62, "temp_hi": None}]
    assert winning_bracket(synthetic) == "≥ 62 °F"


def test_07_empty_day(ctx):
    body = get_ok(ctx["port"], "/markets/NYC_HIGH/2026-06-02/weather")
    assert "No official values loaded." in body
    assert "No weather loaded for this day." in body
    assert "<svg" not in body
    head = heading_text(body)
    assert head.startswith("EV-0602 · Settled high —"), head
    assert "Observed max —" in head and "Winning bracket not settled" in head, head
    assert "bold cells" not in body and "Forecast high per model and lead day (0 = latest run)." in body
    assert table_rows(section(body, "Forecast highs")) == []


def test_08_404s(ctx):
    for path in ("/markets/NYC_HIGH/2026-07-01/weather", "/markets/XXX/2026-06-01/weather"):
        status, _ = fetch(ctx["port"], path)
        assert status == 404, (path, status)


def test_09_day_page_link(ctx):
    body = get_ok(ctx["port"], "/markets/NYC_HIGH/2026-06-01")
    assert 'href="/markets/NYC_HIGH/2026-06-01/weather"' in body
    assert "Weather →</a>" in body


def test_11_live_untouched(ctx):
    after = live_counts()
    assert after == ctx["live_before"], (ctx["live_before"], after)
    server, port = start("kalbt")
    try:
        body = get_ok(port, "/markets/NYC_HIGH/2026-11-22/weather")
    finally:
        server.shutdown()
        server.server_close()
    rows = table_rows(section(body, "Forecast highs"))
    assert len(rows) == 9, [r[0][1] for r in rows]
    assert "Settled high 61 °F" in heading_text(body), heading_text(body)
    assert live_counts() == ctx["live_before"], "counts changed after the GET"


# ---------------------------------------------------------------------------
# Extra attacks
# ---------------------------------------------------------------------------

def xpage(day):
    return f"/markets/XW_HIGH/2026-06-{day}/weather"


def test_e01_forecast_only_day(ctx):
    body = get_ok(ctx["xport"], xpage(10))
    found = chart_points(body)
    assert len(found["t-forecast"]) == 2 and "t-observed" not in found, found.keys()
    assert "No weather loaded" not in body
    assert 'class="t-settled"' not in body, "no settled high, no dashed line"
    assert "No official values loaded." in body
    head = heading_text(body)
    assert "Settled high —" in head and "Observed max —" in head, head
    assert all_inside(body)
    rows = table_rows(section(body, "Forecast highs"))
    assert [r[0][1] for r in rows] == MODELS and rows[0][2][1] == "66.0", rows
    assert all("hit" not in css for r in rows for css, _ in r)


def test_e02_all_null_observations(ctx):
    body = get_ok(ctx["xport"], xpage(11))
    assert "No weather loaded for this day." in body
    assert "<svg" not in body
    assert "Observed max —" in heading_text(body)
    rows = table_rows(section(body, "Forecast highs"))
    assert [(r[0][1], r[1][1]) for r in rows] == [("gfs_seamless", "70.0")], rows


def test_e03_unknown_source_escaped(ctx):
    # the schema only allows kalshi and nws_cli, so the section function gets rows directly
    row = {"source": 'weird<b>&"', "high_f": D("70"), "high_time_lst": '<i>1 PM</i>', "low_f": None,
           "issued_at": datetime(2026, 6, 2, 3, 4, tzinfo=UTC)}
    out = official_section([row])
    assert "weird&lt;b&gt;&amp;&quot;" in out and "<b>" not in out and "<i>" not in out, out
    assert "&lt;i&gt;1 PM&lt;/i&gt;" in out
    assert "2026-06-02 03:04" in out


def test_e04_only_nws_official(ctx):
    body = get_ok(ctx["xport"], xpage(12))
    assert "Settled high 62 °F" in heading_text(body), heading_text(body)
    rows = table_rows(section(body, "Official values"))
    assert [[t for _, t in r] for r in rows] == [
        ["NWS climate report", "62", "1 PM", "50", "2026-06-13 06:00"]], rows
    assert "settled high of 62 °F" in body


def test_e05_kalshi_beats_nws(ctx):
    body = get_ok(ctx["xport"], xpage(13))
    assert "Settled high 61 °F" in heading_text(body), heading_text(body)
    assert "settled high of 61 °F" in body


def test_e06_hit_rounding_at_half(ctx):
    body = get_ok(ctx["xport"], xpage(13))
    rows = table_rows(section(body, "Forecast highs"))
    cells = rows[0]
    # the cell shows the max rounded half up to 1 decimal; hit = shown value rounded to a whole degree
    assert cells[2] == ("num hit", "60.5"), cells[2]   # 60.5: hit
    assert cells[3] == ("num", "61.5"), cells[3]       # 61.5: no hit
    assert cells[4] == ("num", "61.5"), cells[4]       # 61.49 shows 61.5: no hit
    assert cells[5] == ("num hit", "60.5"), cells[5]   # 60.49 shows 60.5: hit
    assert cells[6] == ("num", "61.5"), cells[6]       # 61.45 shows 61.5: no hit
    assert all(css == "num" for css, _ in cells[7:]), cells


def test_e07_extreme_chart_inside(ctx):
    body = get_ok(ctx["xport"], xpage(14))
    assert all_inside(body)
    found = chart_points(body)
    assert len(found["t-observed"][0]) == 3 and len(found["t-forecast"]) == 1
    assert body.count('class="t-settled"') == 1
    ys = [y for pts in found["t-observed"] for _, y in pts]
    assert min(ys) < 30 and max(ys) > 170, ys   # the extremes really span the chart
    # direct call with the same extremes and with a settled high outside the data
    obs = [{"observed_at": ts(14, 6), "temp_f": D(-20)}, {"observed_at": ts(14, 12), "temp_f": D(115)}]
    svg = temp_chart(obs, [], D(140))
    assert all_inside(svg)
    svg = temp_chart(obs, [], D(-60))
    assert all_inside(svg)


def test_e08_model_name_escaped(ctx):
    body = get_ok(ctx["xport"], xpage(14))
    rows = table_rows(section(body, "Forecast highs"))
    assert rows[0][0][1] == UGLY, rows[0][0]
    raw = section(body, "Forecast highs")
    assert f">{ESCAPED}<" in raw and f">{UGLY}<" not in raw


def test_e09_single_point_chart(ctx):
    body = get_ok(ctx["xport"], xpage(15))
    found = chart_points(body)
    assert len(found["t-observed"][0]) == 1, found
    assert all_inside(body)
    assert "Observed max 70.0 °F" in heading_text(body)


def test_e10_parallel(ctx):
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda _: fetch(ctx["port"], PAGE1), range(10)))
    assert [s for s, _ in results] == [200] * 10
    assert len({b for _, b in results}) == 1, "bodies differ"


def test_e11_method_and_odd_paths(ctx):
    status, _ = fetch(ctx["port"], PAGE1, "POST")
    assert status in (404, 405, 501), status
    for path in ("/markets/nyc_high/2026-06-01/weather", "/markets/NYC_HIGH/2026-13-45/weather",
                 "/markets/NYC_HIGH/2026-06-01/weather/", "/markets/NYC_HIGH/2026-06-01/weatherx",
                 "/markets/NYC_HIGH/2026-6-1/weather"):
        status, _ = fetch(ctx["port"], path)
        assert status == 404, (path, status)
    assert fetch(ctx["port"], PAGE1 + "?x=1")[0] == 200


def test_e12_page_shell(ctx):
    body = get_ok(ctx["port"], PAGE1)
    assert "<title>NYC_HIGH · 2026-06-01 · weather · KalBacktester</title>" in body
    assert '<a href="/markets" aria-current="page">Markets</a>' in body
    assert body.index("Official values") < body.index("Temperature through the day") < body.index(
        "<h2>Forecast highs")
    assert ("Observed temperature (blue) and each model&#x27;s day-ahead forecast (grey); "
            "the dashed line is the settled high.") in body or (
            "each model's day-ahead forecast (grey); the dashed line is the settled high." in body)
    assert "Forecast high per model and lead day (0 = latest run); bold cells round" in body


def test_e13_css(ctx):
    css = (ROOT / "ui" / "static" / "app.css").read_text()
    for rule in (".t-observed", ".t-forecast", ".t-settled", ".t-axis", "td.hit"):
        assert css.count(rule) == 1, rule


def main():
    live_before = live_counts()
    admin = connect()
    admin.autocommit = True
    conns, servers = [], []
    try:
        for name in (DB, XDB):
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        conn = make_db(admin, DB)
        conns.append(conn)
        build_main(conn)
        xconn = make_db(admin, XDB)
        conns.append(xconn)
        build_extra(xconn)
        server, port = start(DB)
        xserver, xport = start(XDB)
        servers += [server, xserver]
        ctx = {"conn": conn, "port": port, "xport": xport, "live_before": live_before}

        run_test("01 weather page 200; settled 61 F, winning bracket 60-61 F", test_01_page_and_heading, ctx)
        run_test("02 official table: Kalshi and NWS rows, 300 PM, low 54", test_02_official_table, ctx)
        run_test("03 chart: 3 observed points, 2 forecast lines, 1 settled line, inside box", test_03_chart, ctx)
        run_test("04 forecast highs: model order, hit cells, missing leads, observed max", test_04_forecast_highs_table, ctx)
        run_test("05 forecast_highs equals the hand maxima; [] gives {}", test_05_forecast_highs_function, ctx)
        run_test("06 winning_bracket label and 'not settled'", test_06_winning_bracket, ctx)
        run_test("07 day without weather: both empty messages, settled high missing", test_07_empty_day, ctx)
        run_test("08 404s: unknown date and unknown city", test_08_404s, ctx)
        run_test("09 C08 day page links to the weather page", test_09_day_page_link, ctx)
        run_test("11 live kalbt counts unchanged; live weather page has 9 models", test_11_live_untouched, ctx)
        run_test("E01 forecast-only day still draws forecast lines", test_e01_forecast_only_day, ctx)
        run_test("E02 all-NULL observations, no lead 1: 'No weather' message", test_e02_all_null_observations, ctx)
        run_test("E03 unknown official source is shown raw and escaped", test_e03_unknown_source_escaped, ctx)
        run_test("E04 only nws_cli official: settled high falls back to it", test_e04_only_nws_official, ctx)
        run_test("E05 kalshi wins over nws_cli for the settled high", test_e05_kalshi_beats_nws, ctx)
        run_test("E06 hit uses the shown value (60.5, 60.49 hit; 61.5, 61.49, 61.45 no hit for 61)", test_e06_hit_rounding_at_half, ctx)
        run_test("E07 extreme temperatures (-20, 115) stay inside 0..640 x 0..200", test_e07_extreme_chart_inside, ctx)
        run_test("E08 model name with <>&\" is escaped", test_e08_model_name_escaped, ctx)
        run_test("E09 single observation: no crash, inside the box", test_e09_single_point_chart, ctx)
        run_test("E10 10 parallel requests all 200 and equal", test_e10_parallel, ctx)
        run_test("E11 POST and odd paths", test_e11_method_and_odd_paths, ctx)
        run_test("E12 page shell, section order, sentences", test_e12_page_shell, ctx)
        run_test("E13 css rules present once", test_e13_css, ctx)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for conn in conns:
            conn.close()
        for name in (DB, XDB):
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
