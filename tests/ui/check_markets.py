"""Checks the market pages (spec C08, agreed tests 1 to 11, plus extra attacks E01 and up).

Creates two throwaway databases, kalbt_ui_markets_<pid> (schema + portfolio views + the spec's
hand fixture) and kalbt_ui_markets_<pid>_x (schema + portfolio views + odd cases for the extra
attacks), serves them with make_server on free ports, fetches pages over HTTP, and drops both
databases first and at the end (also when a test fails). Expected ladder and quote values are
read with query.market from the same throwaway database, plus hand values. Live kalbt is only
read: SELECT count(*) (test 11) and one GET of a fixed past event (E10).
Run from the repo root: PYTHONPATH=. uv run python tests/ui/check_markets.py
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
from ui.markets import bracket_label, quote_panel
from ui.serve import make_server

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = f"kalbt_ui_markets_{os.getpid()}"
XDB = DB + "_x"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
D = Decimal
UGLY = '<>&"'
ESCAPED = "&lt;&gt;&amp;&quot;"
MISSING_MARK = "—"

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
    """(status, body text) for one raw request."""
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
    """The <section> html whose h2 starts with the given text."""
    for found in re.findall(r"<section.*?</section>", body, flags=re.DOTALL):
        if re.search(rf"<h2>{re.escape(heading)}", found):
            return found
    raise AssertionError(f"no section {heading!r}")


def table_rows(fragment):
    """Body rows as (row opening tag, [raw cell html]); header rows are skipped."""
    rows = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", fragment, flags=re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        if cells:
            rows.append((re.match(r"<tr[^>]*>", row).group(0), cells))
    return rows


def panels(body):
    """The html of every quote panel figure."""
    return re.findall(r"<figure class=\"q-panel.*?</figure>", body, flags=re.DOTALL)


def points(figure):
    """Every (x, y) polyline point of one panel."""
    found = []
    for attr in re.findall(r'<polyline[^>]* points="([^"]*)"', figure):
        found += [tuple(float(n) for n in p.split(",")) for p in attr.split()]
    return found


def fmt_price(value):
    """Independent price formatting: 2 decimals, or 4 when 2 would lose digits."""
    if value is None:
        return MISSING_MARK
    if value == value.quantize(D("0.01")):
        return f"{value:.2f}"
    return f"{value:.4f}"


def ts(day, hour, minute):
    return datetime(2026, 6, day, hour, minute, tzinfo=UTC)


def live_counts():
    conn = connect()
    try:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in LIVE_TABLES}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def add_station(conn, station_id):
    conn.execute(
        "INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h,"
        " iem_id, iem_network) VALUES (%s, %s, 40, -74, 'America/New_York', -5, %s, 'NY_ASOS')",
        (station_id, "Station " + station_id, station_id[1:]),
    )


def add_city(conn, underlying_id, description, station_id, series):
    add_station(conn, station_id)
    conn.execute("INSERT INTO underlying (underlying_id, description, station_id) VALUES (%s, %s, %s)",
                 (underlying_id, description, station_id))
    conn.execute("INSERT INTO series (ticker, underlying_id, title) VALUES (%s, %s, 'title')",
                 (series, underlying_id))


def add_event(conn, series, underlying_id, event_date, event_ticker):
    conn.execute(
        "INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
        " VALUES (%s, %s, %s, %s, 'src')", (event_ticker, series, underlying_id, event_date))


def add_market(conn, ticker, event_ticker, strike_type, floor, cap, result):
    settled_at = datetime(2026, 6, 20, tzinfo=UTC) if result else None
    conn.execute(
        "INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike, open_time,"
        " close_time, result, settled_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (ticker, event_ticker, strike_type, floor, cap, datetime(2026, 5, 1, tzinfo=UTC),
         datetime(2026, 6, 19, tzinfo=UTC), result, settled_at))


def add_candles(conn, market_ticker, candles):
    for when, bid, ask, volume in candles:
        conn.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                     " VALUES (%s,%s,%s,%s,%s)", (market_ticker, when, bid, ask, volume))


def build_main(conn):
    add_city(conn, "NYC_HIGH", "New York City high", "KNYC", "KXHIGHNY")
    add_city(conn, "CHI_HIGH", "Chicago high", "KMDW", "KXHIGHCHI")
    add_event(conn, "KXHIGHNY", "NYC_HIGH", date(2026, 6, 1), "KXHIGHNY-26JUN01")
    add_market(conn, "KXHIGHNY-26JUN01-T60", "KXHIGHNY-26JUN01", "less", None, 60, "no")
    add_market(conn, "KXHIGHNY-26JUN01-B60", "KXHIGHNY-26JUN01", "between", 60, 61, "yes")
    add_market(conn, "KXHIGHNY-26JUN01-T61", "KXHIGHNY-26JUN01", "greater", 61, None, "no")
    conn.execute(
        "INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)"
        " VALUES ('KNYC', '2026-06-01', 'kalshi', 61, %s, 'KXHIGHNY-26JUN01')", (ts(2, 0, 0),))
    add_candles(conn, "KXHIGHNY-26JUN01-T60", [
        (ts(1, 12, 0), D("0.30"), D("0.35"), 5), (ts(1, 12, 10), D("0.32"), D("0.36"), 3),
        (ts(1, 12, 40), D("0.31"), D("0.3333"), 2)])
    add_candles(conn, "KXHIGHNY-26JUN01-B60", [
        (ts(1, 12, 5), D("0.50"), D("0.55"), 10), (ts(1, 12, 20), D("0.60"), D("0.65"), 4),
        (ts(1, 13, 0), D("0.90"), D("0.95"), 7)])
    add_event(conn, "KXHIGHNY", "NYC_HIGH", date(2026, 6, 2), "KXHIGHNY-26JUN02")
    add_market(conn, "KXHIGHNY-26JUN02-T60", "KXHIGHNY-26JUN02", "less", None, 60, None)
    add_market(conn, "KXHIGHNY-26JUN02-B60", "KXHIGHNY-26JUN02", "between", 60, 61, None)
    conn.commit()


def build_extra(conn):
    # XA: odd description, one event without strike types, one with a NULL bid and an odd ticker
    add_city(conn, "XA_HIGH", f"Odd {UGLY} city", "KXA", "KXA")
    add_event(conn, "KXA", "XA_HIGH", date(2026, 6, 5), "KXA-26JUN05")
    add_market(conn, "KXA-26JUN05-N1", "KXA-26JUN05", None, None, None, None)
    add_market(conn, "KXA-26JUN05-N2", "KXA-26JUN05", None, None, None, None)
    add_candles(conn, "KXA-26JUN05-N1", [(ts(5, 9, 0), D("0.20"), D("0.25"), 1),
                                         (ts(5, 10, 0), D("0.22"), D("0.27"), 1)])
    add_event(conn, "KXA", "XA_HIGH", date(2026, 6, 6), "KXA-26JUN06")
    odd = f"KXA-26JUN06-{UGLY}"
    add_market(conn, odd, "KXA-26JUN06", "between", 70, 71, None)
    add_market(conn, "KXA-26JUN06-T70", "KXA-26JUN06", "less", None, 70, None)
    add_candles(conn, odd, [(ts(6, 9, 0), None, D("0.40"), 2), (ts(6, 9, 20), None, D("0.45"), 1),
                            (ts(6, 10, 0), None, D("0.50"), 1)])
    add_candles(conn, "KXA-26JUN06-T70", [(ts(6, 9, 0), D("0.10"), D("0.15"), 1),
                                          (ts(6, 10, 0), D("0.11"), D("0.16"), 1)])
    # a city with a 40 character id, one with 41
    add_city(conn, "A" * 40, "forty", "KA40", "KA40")
    add_event(conn, "KA40", "A" * 40, date(2026, 6, 7), "KA40-26JUN07")
    add_market(conn, "KA40-26JUN07-M", "KA40-26JUN07", "less", None, 50, None)
    add_city(conn, "B" * 41, "forty-one", "KB41", "KB41")
    add_event(conn, "KB41", "B" * 41, date(2026, 6, 7), "KB41-26JUN07")
    add_market(conn, "KB41-26JUN07-M", "KB41-26JUN07", "less", None, 50, None)
    conn.commit()


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

DAY1 = "/markets/NYC_HIGH/2026-06-01"
EVENT1 = "KXHIGHNY-26JUN01"


def ladder_table(ctx):
    body = get_ok(ctx["port"], DAY1)
    return body, table_rows(section(body, "Ladder"))


def test_01_cities(ctx):
    body = get_ok(ctx["port"], "/markets")
    rows = [[text_of(c) for c in cells] for _, cells in table_rows(body)]
    assert [r[0] for r in rows] == ["CHI_HIGH", "NYC_HIGH"], rows  # ordered by series ticker
    assert rows[0] == ["CHI_HIGH", "Chicago high", "KMDW", "KXHIGHCHI"], rows[0]
    assert rows[1] == ["NYC_HIGH", "New York City high", "KNYC", "KXHIGHNY"], rows[1]
    for city in ("CHI_HIGH", "NYC_HIGH"):
        assert f'<a href="/markets/{city}">{city}</a>' in body
    assert '<a href="/markets" aria-current="page">Markets</a>' in body, "nav"
    assert '<a href="/runs">Runs</a>' in body, "runs link not current"


def test_02_days(ctx):
    body = get_ok(ctx["port"], "/markets/NYC_HIGH")
    rows = [[text_of(c) for c in cells] for _, cells in table_rows(body)]
    assert rows == [["2026-06-02", "KXHIGHNY-26JUN02", "2", "Open"],
                    ["2026-06-01", EVENT1, "3", "Settled"]], rows
    assert '<a href="/markets/NYC_HIGH/2026-06-02">' in body
    assert "NYC_HIGH · markets" in body


def test_03_ladder(ctx):
    body, rows = ladder_table(ctx)
    texts = [[text_of(c) for c in cells] for _, cells in rows]
    assert [r[1] for r in texts] == ["≤ 59 °F", "60–61 °F", "≥ 62 °F"], texts
    assert [r[0] for r in texts] == ["1", "2", "3"]
    assert [r[2] for r in texts] == ["lower_tail", "middle", "upper_tail"]
    assert [r[3] for r in texts] == ["No", "YES", "No"], texts
    assert ["row-win" in tag for tag, _ in rows] == [False, True, False], [t for t, _ in rows]
    assert "settled high" in body and "61 °F" in text_of(body.split("settled high")[1].split("</p>")[0])


def test_04_last_values(ctx):
    _, rows = ladder_table(ctx)
    ladder = q.ladder(ctx["conn"], EVENT1)
    quotes = q.quotes(ctx["conn"], EVENT1)
    assert len(ladder) == len(rows) == 3
    for market, (_, cells) in zip(ladder, rows, strict=True):
        mine = [r for r in quotes if r["market_ticker"] == market["market_ticker"]]
        texts = [text_of(c) for c in cells]
        assert texts[4] == fmt_price(mine[-1]["yes_bid"]), (market["market_ticker"], texts)
        assert texts[5] == fmt_price(mine[-1]["yes_ask"]), (market["market_ticker"], texts)
        assert texts[6] == f"{sum(r['volume'] for r in mine):,.0f}", (market["market_ticker"], texts)
    # hand values for the last bid and ask; volume is only compared with the query function
    texts = [[text_of(c) for c in cells] for _, cells in rows]
    assert texts[0][4:6] == ["0.31", "0.3333"], texts[0]
    assert texts[1][4:6] == ["0.90", "0.95"], texts[1]
    assert texts[2][4:] == [MISSING_MARK, MISSING_MARK, "0"], texts[2]


def test_05_panels(ctx):
    body = get_ok(ctx["port"], DAY1)
    found = panels(body)
    assert len(found) == 3, len(found)
    wins = [("q-panel-win" in f.split(">")[0]) for f in found]
    assert wins == [False, True, False], wins
    assert "B60" in found[1] and "60–61 °F" in found[1]
    for figure in found:
        for x, y in points(figure):
            assert 0 <= x <= 200 and 0 <= y <= 80, (x, y)
    assert "<polyline" in found[0] and "<polyline" in found[1]
    assert "<polyline" not in found[2], "market without candles has a polyline"
    assert 'aria-label="≥ 62 °F bid and ask, 0 to 1"' in found[2]


def test_06_no_quotes(ctx):
    body = get_ok(ctx["port"], "/markets/NYC_HIGH/2026-06-02")
    assert "No quotes loaded for this day." in body
    assert not panels(body), "panels on a day without candles"
    ladder = [[text_of(c) for c in cells] for _, cells in table_rows(section(body, "Ladder"))]
    assert [r[3] for r in ladder] == ["Open", "Open"], ladder
    assert "settled high" in body and MISSING_MARK in body.split("settled high")[1].split("</p>")[0]


def test_07_bracket_label(ctx):
    assert bracket_label(60, 61) == "60–61 °F"
    assert bracket_label(None, 59) == "≤ 59 °F"
    assert bracket_label(62, None) == "≥ 62 °F"
    assert bracket_label(None, None) == "no range"


def test_08_404s(ctx):
    for path in ("/markets/XXX", "/markets/NYC_HIGH/2026-07-01", "/markets/NYC_HIGH/2026-13-40",
                 "/markets/nyc;drop/2026-06-01", "/markets/" + "A" * 41):
        status, _ = fetch(ctx["port"], path)
        assert status == 404, (path, status)


def test_09_escaping(ctx):
    body = get_ok(ctx["xport"], "/markets")
    assert f"Odd {ESCAPED} city" in body, "description not escaped"
    assert f"Odd {UGLY} city" not in body


def test_11_live_untouched(ctx):
    after = live_counts()
    assert after == ctx["live_before"], (ctx["live_before"], after)


# ---------------------------------------------------------------------------
# Extra attacks
# ---------------------------------------------------------------------------

def test_e01_city_without_events(ctx):
    body = get_ok(ctx["port"], "/markets/CHI_HIGH")
    assert "No events loaded for this city." in body
    assert "CHI_HIGH · markets" in body


def test_e02_no_strike_type(ctx):
    body = get_ok(ctx["xport"], "/markets/XA_HIGH/2026-06-05")
    rows = [[text_of(c) for c in cells] for _, cells in table_rows(section(body, "Ladder"))]
    assert [r[1] for r in rows] == ["no range", "no range"], rows
    found = panels(body)
    assert len(found) == 2
    assert "<figcaption>no range " in found[0]
    assert 'aria-label="no range bid and ask, 0 to 1"' in found[0]
    assert "<polyline" in found[0] and "<polyline" not in found[1]


def test_e03_null_bid_with_ask(ctx):
    body = get_ok(ctx["xport"], "/markets/XA_HIGH/2026-06-06")
    found = panels(body)
    odd = next(f for f in found if ESCAPED in f)
    assert 'class="q-yes_ask"' in odd and 'class="q-yes_bid"' not in odd, odd
    ladder = table_rows(section(body, "Ladder"))
    last = next([text_of(c) for c in cells] for _, cells in ladder if ESCAPED in cells[0] or "70–71" in cells[1])
    assert last[4] == MISSING_MARK and last[5] == "0.50", last
    # a bid gap in the middle of a run splits the line in two
    svg = quote_panel("T", "L", [
        {"ts": ts(1, 0, 0), "yes_bid": D("0.4"), "yes_ask": D("0.5"), "volume": 0},
        {"ts": ts(1, 1, 0), "yes_bid": None, "yes_ask": D("0.5"), "volume": 0},
        {"ts": ts(1, 2, 0), "yes_bid": D("0.4"), "yes_ask": D("0.5"), "volume": 0}],
        ts(1, 0, 0), ts(1, 2, 0), False)
    assert svg.count('class="q-yes_bid"') == 2 and svg.count('class="q-yes_ask"') == 1, svg


def test_e04_candles_before_first_step(ctx):
    # the first step is after the first candle; a market whose only data is NULL quotes, and
    # panels with a zero-length time span, must not crash
    rows = [{"ts": ts(1, 12, 0), "yes_bid": None, "yes_ask": None, "volume": 0}]
    svg = quote_panel("T", "L", rows, ts(1, 12, 0), ts(1, 12, 0), False)
    assert "<polyline" not in svg
    one = [{"ts": ts(1, 12, 0), "yes_bid": D("0.4"), "yes_ask": D("0.5"), "volume": 0}]
    svg = quote_panel("T", "L", one, ts(1, 12, 0), ts(1, 12, 0), True)
    assert svg.count("<polyline") == 2 and "q-panel-win" in svg
    assert svg.count('points="4.0,') == 2, svg
    assert "<polyline" not in quote_panel("T", "L", [], ts(1, 12, 0), ts(1, 13, 0), False)
    # first candle of the day at 12:00; the first step of q.quotes is on or after it
    first = min(row["ts"] for row in q.quotes(ctx["conn"], EVENT1))
    assert first >= ts(1, 12, 0), first
    assert get_ok(ctx["port"], DAY1)


def test_e05_x_extremes(ctx):
    body = get_ok(ctx["port"], DAY1)
    xs = [x for f in panels(body) for x, _ in points(f)]
    assert min(xs) == 4.0 and max(xs) == 196.0, (min(xs), max(xs))
    rows = [{"ts": ts(1, 12, 0), "yes_bid": D("0.4"), "yes_ask": D("0.5"), "volume": 0},
            {"ts": ts(1, 13, 0), "yes_bid": D("0.4"), "yes_ask": D("0.5"), "volume": 0}]
    svg = quote_panel("T", "L", rows, ts(1, 12, 0), ts(1, 13, 0), False)
    assert 'points="4.0,40.0 196.0,40.0"' in svg, svg


def test_e06_city_id_length(ctx):
    status, body = fetch(ctx["xport"], "/markets/" + "A" * 40)
    assert status == 200 and "A" * 40 in body, status
    assert fetch(ctx["xport"], "/markets/" + "A" * 40 + "/2026-06-07")[0] == 200
    assert fetch(ctx["xport"], "/markets/" + "B" * 41)[0] == 404
    assert fetch(ctx["xport"], "/markets/" + "B" * 41 + "/2026-06-07")[0] == 404


def test_e07_lowercase_and_odd_paths(ctx):
    for path in ("/markets/nyc_high", "/markets/nyc_high/2026-06-01", "/markets/NYC_HIGH/2026-6-1",
                 "/markets/NYC_HIGH/2026-06-01/", "/markets/NYC_HIGH/2026-06-01/x", "/markets//2026-06-01",
                 "/markets/NYC_HIGH%0A", "/markets/NYC_HIGH/2026-02-30"):
        status, _ = fetch(ctx["port"], path)
        assert status == 404, (path, status)
    assert fetch(ctx["port"], "/markets?x=1")[0] == 200
    assert fetch(ctx["port"], "/markets", "POST")[0] == 405


def test_e08_ticker_escaping(ctx):
    body = get_ok(ctx["xport"], "/markets/XA_HIGH/2026-06-06")
    assert f"KXA-26JUN06-{UGLY}" not in body, "raw ticker"
    assert f"KXA-26JUN06-{ESCAPED}" in body
    for tag in re.findall(r"<svg[^>]*>", body):
        assert UGLY not in tag


def test_e09_css(ctx):
    css = (ROOT / "ui" / "static" / "app.css").read_text()
    lines = css.splitlines()
    assert len([ln for ln in lines if re.match(r"\.panel\s*\{", ln)]) == 1, ".panel rule count"
    for selector in (".q-panel ", ".q-panel-win ", ".q-panel figcaption ", ".q-panel svg ",
                     ".q-yes_ask ", ".q-yes_bid ", ".q-mid ", ".row-win td "):
        assert any(ln.startswith(selector + "{") for ln in lines), selector
    assert get_ok(ctx["port"], DAY1).count('class="panel"') >= 1


def test_e10_live_fixed_event(ctx):
    server, port = start("kalbt")
    try:
        body = get_ok(port, "/markets/NYC_HIGH/2026-11-22")
        assert len(panels(body)) == 6, len(panels(body))
        assert len(table_rows(section(body, "Ladder"))) == 6
        assert get_ok(port, "/markets").count("<tr") >= 2
    finally:
        server.shutdown()
        server.server_close()


def test_e11_parallel(ctx):
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda _: fetch(ctx["port"], DAY1), range(10)))
    assert {status for status, _ in results} == {200}, [s for s, _ in results]
    assert len({body for _, body in results}) == 1, "bodies differ"


def test_e12_page_shell(ctx):
    body = get_ok(ctx["port"], DAY1)
    assert '<a href="/markets/NYC_HIGH">← NYC_HIGH · markets</a>' in body
    assert "<h1>NYC_HIGH · 2026-06-01</h1>" in body
    assert "<title>" in body and "NYC_HIGH · 2026-06-01" in body.split("</title>")[0]
    first = min(row["ts"] for row in q.quotes(ctx["conn"], EVENT1))
    assert f"Bid and ask every 15 minutes from {first:%Y-%m-%d %H:%M} to " in body
    assert "UTC; all panels share the 0–1 price axis." in body
    assert 'aria-current="page">Markets' in body


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def make_db(admin, name):
    admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {name}")
    conn = connect(name)
    conn.execute(SCHEMA.read_text())
    conn.execute(PORTFOLIO.read_text())
    conn.commit()
    return conn


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

        run_test("01 /markets lists CHI_HIGH and NYC_HIGH as links; nav is current", test_01_cities, ctx)
        run_test("02 days page: 2026-06-02 Open then 2026-06-01 Settled", test_02_days, ctx)
        run_test("03 ladder order, winning row, settled high 61 F", test_03_ladder, ctx)
        run_test("04 last bid / ask / volume equal the query function", test_04_last_values, ctx)
        run_test("05 three panels, winner marked, points inside 0..200 x 0..80", test_05_panels, ctx)
        run_test("06 day without candles: no quotes sentence", test_06_no_quotes, ctx)
        run_test("07 bracket_label", test_07_bracket_label, ctx)
        run_test("08 404s", test_08_404s, ctx)
        run_test("09 description with <>&\" is escaped", test_09_escaping, ctx)
        run_test("11 live kalbt row counts unchanged", test_11_live_untouched, ctx)
        run_test("E01 city with a station but no events", test_e01_city_without_events, ctx)
        run_test("E02 markets without strike type: 'no range'", test_e02_no_strike_type, ctx)
        run_test("E03 NULL bid with ask: ask line, bid gap", test_e03_null_bid_with_ask, ctx)
        run_test("E04 empty, single-point and zero-span panels do not crash", test_e04_candles_before_first_step, ctx)
        run_test("E05 earliest and latest ts give x = 4 and 196", test_e05_x_extremes, ctx)
        run_test("E06 city id of 40 chars works, 41 is 404", test_e06_city_id_length, ctx)
        run_test("E07 lower case and odd paths 404, query ok, POST 405", test_e07_lowercase_and_odd_paths, ctx)
        run_test("E08 market ticker with <>&\" is escaped", test_e08_ticker_escaping, ctx)
        run_test("E09 .panel once in app.css, q-panel rules present", test_e09_css, ctx)
        run_test("E10 live kalbt: NYC_HIGH 2026-11-22 has 6 panels (read only)", test_e10_live_fixed_event, ctx)
        run_test("E11 10 parallel requests all 200 and equal", test_e11_parallel, ctx)
        run_test("E12 page shell, back link, caption sentence", test_e12_page_shell, ctx)
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
