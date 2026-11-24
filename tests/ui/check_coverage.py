"""Checks the coverage page (spec C10, agreed tests 1 to 7, plus extra attacks E01 and up).

Creates two throwaway databases, kalbt_ui_coverage_<pid> (the spec's hand fixture) and
kalbt_ui_coverage_<pid>_x (several Check weeks and a city whose id has <>&"), both from
db/schema.sql and db/portfolio.sql, and drops both first and in finally. Expected numbers are
read with query.coverage from the same throwaway database, plus the hand statuses. Live kalbt
is only read: SELECT count(*) (test 7) and GETs of /coverage (test 7, E07).
Run from the repo root: PYTHONPATH=. uv run python tests/ui/check_coverage.py
"""
import html
import http.client
import os
import re
import threading
import time
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from db.connect import connect
from query import coverage as cov
from ui import coverage as page_mod
from ui.layout import MISSING
from ui.serve import make_server

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = f"kalbt_ui_coverage_{os.getpid()}"
XDB = DB + "_x"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
KINDS = {"OK": "ok", "Check": "warn", "RED": "red"}
UGLY = '<>&"'
ESCAPED = "&lt;&gt;&amp;&quot;"

OK_WEEK = date(2026, 9, 7)       # A_HIGH: OK
CHECK_WEEK = date(2026, 9, 14)   # B_HIGH: Check
EXTRA_WEEK = date(2026, 6, 1)    # C_HIGH only

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

def fetch(port, path):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path)
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
    """Body rows as (row opening tag, [(cell opening tag, cell html)])."""
    rows = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", fragment, flags=re.DOTALL):
        cells = re.findall(r"(<td[^>]*>)(.*?)</td>", row, flags=re.DOTALL)
        if cells:
            rows.append((re.match(r"<tr[^>]*>", row).group(0), cells))
    return rows


def grid(body):
    """(weeks, {city: {week: cell html}}) from the Grid section."""
    fragment = section(body, "Grid")
    weeks = re.findall(r'<th><span class="mono">(\d{4}-\d{2}-\d{2})</span></th>', fragment)
    cells = {}
    for _tag, row in table_rows(fragment):
        city = text_of(row[0][1])
        assert len(row) == len(weeks) + 1, f"{city}: {len(row)} cells for {len(weeks)} weeks"
        cells[city] = {week: cell for week, (_t, cell) in zip(weeks, row[1:], strict=True)}
    return weeks, cells


def attention_rows(body):
    """[(city, week, chip word, [numbers], [cell tags])] from Needs attention."""
    out = []
    for _tag, row in table_rows(section(body, "Needs attention")):
        texts = [text_of(c) for _t, c in row]
        out.append((texts[0], texts[1], texts[2], texts[3:], [t for t, _c in row]))
    return out


def sort_key_expected(r):
    return ({"RED": 0, "Check": 1}[r["status"]], r["underlying_id"], r["week_start"])


def live_counts():
    conn = connect()
    try:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in LIVE_TABLES}
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def add_city(conn, uid, station, series):
    conn.execute(
        "INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h, iem_id, iem_network)"
        " VALUES (%s, %s, 40, -74, 'America/New_York', -5, %s, 'NY_ASOS')",
        (station, station, station[1:]),
    )
    conn.execute("INSERT INTO underlying (underlying_id, description, station_id) VALUES (%s, %s, %s)",
                 (uid, uid, station))
    conn.execute("INSERT INTO series (ticker, underlying_id, title) VALUES (%s, %s, %s)", (series, uid, series))


def add_day(conn, uid, station, series, day, nws=True, kalshi=True, kalshi_high=70):
    event = f"{series}-{day}"
    conn.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                 " VALUES (%s, %s, %s, %s, 'nws')", (event, series, uid, day))
    market = event + "-T60"
    t0 = datetime(day.year, day.month, day.day, 5, tzinfo=UTC)
    conn.execute(
        "INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, open_time, close_time, result, settled_at)"
        " VALUES (%s, %s, 'greater', 60, %s, %s, 'yes', %s)",
        (market, event, t0, t0 + timedelta(days=1), t0 + timedelta(days=2)),
    )
    for minute in (0, 1):
        conn.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume) VALUES (%s, %s, 0.4, 0.5, 3)",
                     (market, t0 + timedelta(minutes=minute)))
    issued = t0 + timedelta(days=2)
    if nws:
        conn.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst, low_f,"
                     " issued_at, reference) VALUES (%s, %s, 'nws_cli', 70, '2 PM', 50, %s, 'ref')",
                     (station, day, issued))
    if kalshi:
        conn.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at, reference)"
                     " VALUES (%s, %s, 'kalshi', %s, %s, 'ref')", (station, day, kalshi_high, issued))


def add_week(conn, uid, station, series, monday, mismatch_day=None, missing_nws_days=0):
    for i in range(7):
        day = monday + timedelta(days=i)
        add_day(conn, uid, station, series, day,
                nws=i >= missing_nws_days,
                kalshi_high=71 if i == mismatch_day else 70)


def build_main(conn):
    add_city(conn, "A_HIGH", "KAAA", "KXAHIGH")
    add_city(conn, "B_HIGH", "KBBB", "KXBHIGH")
    add_city(conn, "C_HIGH", "KCCC", "KXCHIGH")
    add_week(conn, "A_HIGH", "KAAA", "KXAHIGH", OK_WEEK)
    add_week(conn, "B_HIGH", "KBBB", "KXBHIGH", CHECK_WEEK, mismatch_day=3)
    add_day(conn, "C_HIGH", "KCCC", "KXCHIGH", EXTRA_WEEK)
    conn.commit()


def build_extra(conn):
    """Ordering and escaping: Check weeks in two cities, a city id with <>&\"."""
    add_city(conn, "A_HIGH", "KAAA", "KXAHIGH")
    add_city(conn, "B_HIGH", "KBBB", "KXBHIGH")
    add_city(conn, f"Z{UGLY}", "KZZZ", "KXZHIGH")
    add_week(conn, "A_HIGH", "KAAA", "KXAHIGH", date(2026, 9, 14), missing_nws_days=1)
    add_week(conn, "A_HIGH", "KAAA", "KXAHIGH", date(2026, 9, 7), missing_nws_days=2)
    add_week(conn, "B_HIGH", "KBBB", "KXBHIGH", date(2026, 9, 7), mismatch_day=0)
    add_week(conn, f"Z{UGLY}", "KZZZ", "KXZHIGH", date(2026, 9, 7))
    conn.commit()


def make_db(admin, name):
    admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {name}")
    conn = connect(name)
    conn.execute(SCHEMA.read_text())
    conn.execute(PORTFOLIO.read_text())
    conn.commit()
    return conn


def by_key(rows):
    return {(r["underlying_id"], r["week_start"]): r for r in rows}


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

def test_01_summary_and_nav(ctx):
    rows = cov.coverage(ctx["conn"])
    cells = by_key(rows)
    assert cells["A_HIGH", OK_WEEK]["status"] == "OK", cells["A_HIGH", OK_WEEK]
    assert cells["B_HIGH", CHECK_WEEK]["status"] == "Check", cells["B_HIGH", CHECK_WEEK]
    assert cells["C_HIGH", EXTRA_WEEK]["status"] == "RED"
    n = {s: sum(1 for r in rows if r["status"] == s) for s in KINDS}
    assert n["OK"] == 1 and n["Check"] == 1, n
    weeks = sorted({r["week_start"] for r in rows})
    body = get_ok(ctx["port"], "/coverage")
    expected = (f"{len(rows)} city-weeks · {n['OK']} OK · {n['Check']} Check · {n['RED']} RED"
                f" · weeks {weeks[0]} – {weeks[-1]}")
    assert expected in text_of(body), f"expected {expected!r}"
    assert "<title>Coverage" in body or "Coverage" in re.search(r"<title>(.*?)</title>", body).group(1)
    assert re.search(r'<a href="/coverage" aria-current="page">Coverage</a>', body)
    assert 'href="/runs" aria-current' not in body and 'href="/markets" aria-current' not in body


def test_02_grid(ctx):
    rows = cov.coverage(ctx["conn"])
    cells = by_key(rows)
    body = get_ok(ctx["port"], "/coverage")
    weeks, grid_cells = grid(body)
    exp_weeks = sorted({r["week_start"] for r in rows})
    assert weeks == [str(w) for w in exp_weeks], weeks
    assert len(weeks) == 14, len(weeks)  # 13 window weeks + 2026-06-01
    assert list(grid_cells) == ["A_HIGH", "B_HIGH", "C_HIGH"], list(grid_cells)
    for city in grid_cells:
        for week in exp_weeks:
            cell = grid_cells[city][str(week)]
            row = cells.get((city, week))
            if row is None:
                assert MISSING in cell and "chip" not in cell, (city, week, cell)
                assert city != "C_HIGH" and week == EXTRA_WEEK, (city, week)
                continue
            word = text_of(cell)
            assert word == row["status"], (city, week, word, row["status"])
            assert f'class="chip chip-{KINDS[row["status"]]}"' in cell, (city, week, cell)
            assert f'href="/markets/{city}"' in cell, (city, week, cell)
            title = re.search(r'title="([^"]*)"', cell).group(1)
            exp_title = (f'{row["events"]} events · {row["markets"]} markets · {row["bars"]} bars'
                         f' · NWS {row["nws_highs"]} · Kalshi {row["kalshi_highs"]} · gate – '
                         f'· numeric {row["numeric_mismatches"]}')
            assert html.unescape(title) == exp_title, (city, week, title, exp_title)
    # hand numbers
    ok_title = html.unescape(re.search(r'title="([^"]*)"', grid_cells["A_HIGH"][str(OK_WEEK)]).group(1))
    assert ok_title == "7 events · 7 markets · 14 bars · NWS 7 · Kalshi 7 · gate – · numeric 0", ok_title
    check_title = html.unescape(re.search(r'title="([^"]*)"', grid_cells["B_HIGH"][str(CHECK_WEEK)]).group(1))
    assert check_title == "7 events · 7 markets · 14 bars · NWS 7 · Kalshi 7 · gate – · numeric 1", check_title
    assert MISSING in grid_cells["A_HIGH"][str(EXTRA_WEEK)] and MISSING in grid_cells["B_HIGH"][str(EXTRA_WEEK)]


def test_03_attention(ctx):
    rows = cov.coverage(ctx["conn"])
    expected = sorted((r for r in rows if r["status"] != "OK"), key=sort_key_expected)
    body = get_ok(ctx["port"], "/coverage")
    found = attention_rows(body)
    assert len(found) == len(expected), (len(found), len(expected))
    for got, row in zip(found, expected, strict=True):
        city, week, word, numbers, tags = got
        assert (city, week, word) == (row["underlying_id"], str(row["week_start"]), row["status"]), got
        exp_numbers = [row["events"], row["markets"], row["bars"], row["nws_highs"], row["kalshi_highs"]]
        assert numbers[:5] == [str(n) for n in exp_numbers], (got, exp_numbers)
        assert numbers[5] == "—", got  # gate None here
        assert numbers[6] == str(row["numeric_mismatches"]), got
        assert all(t == '<td class="num">' for t in tags[3:]), tags
        assert tags[0] == '<td class="mono">' and tags[1] == '<td class="mono">', tags
    words = [g[2] for g in found]
    assert words == sorted(words, key=lambda w: {"RED": 0, "Check": 1}[w]), words
    assert words[-1] == "Check" and words.count("Check") == 1, words
    assert found[-1][0] == "B_HIGH" and found[-1][1] == str(CHECK_WEEK)
    assert found[-1][3] == ["7", "7", "14", "7", "7", "—", "1"], found[-1]
    fragment = section(body, "Needs attention")
    heads = re.findall(r"<th([^>]*)>(.*?)</th>", fragment)
    assert [text_of(h) for _a, h in heads] == ["City", "Week", "Status", "Events", "Markets", "Bars",
                                              "NWS highs", "Kalshi highs", "Gate warnings",
                                              "Numeric mismatches"], heads
    assert all('class="num"' in a for a, _h in heads[3:]), heads


def test_04_status_kind(_ctx):
    assert page_mod.status_kind("OK") == "ok"
    assert page_mod.status_kind("Check") == "warn"
    assert page_mod.status_kind("RED") == "red"
    try:
        page_mod.status_kind("other")
    except ValueError:
        pass
    else:
        raise AssertionError("no ValueError for 'other'")


def test_05_empty(_ctx):
    original = page_mod.cov.coverage
    page_mod.cov.coverage = lambda conn: []
    try:
        body = page_mod.coverage_page(None)
    finally:
        page_mod.cov.coverage = original
    assert "No coverage data yet." in body
    assert "<table" not in body and "Grid" not in body and "Needs attention" not in body
    assert "city-weeks" not in body


def test_06_escaping(ctx):
    body = get_ok(ctx["xport"], "/coverage")
    assert f"Z{UGLY}" not in body
    assert f"Z{ESCAPED}" in body
    _weeks, cells = grid(body)
    assert f"Z{UGLY}" in cells, list(cells)  # text_of unescapes
    for href in re.findall(r'href="([^"]*)"', body):
        assert "<" not in href and '"' not in href
    hrefs = [html.unescape(h) for h in re.findall(r'href="(/markets/[^"]*)"', body)]
    assert f"/markets/Z{UGLY}" in hrefs, hrefs


def test_07_live(ctx):
    before = ctx["live_before"]
    body = get_ok(ctx["live_port"], "/coverage")
    weeks, cells = grid(body)
    assert "2026-11-16" in weeks, weeks
    cell = cells["NYC_HIGH"]["2026-11-16"]
    assert 'class="chip chip-ok"' in cell and text_of(cell) == "OK", cell
    after = live_counts()
    assert after == before, (before, after)


# ---------------------------------------------------------------------------
# Extra attacks
# ---------------------------------------------------------------------------

def test_e01_summary_equals_grid_chips(ctx):
    for port in (ctx["port"], ctx["xport"]):
        body = get_ok(port, "/coverage")
        _weeks, cells = grid(body)
        words = [text_of(c) for row in cells.values() for c in row.values() if "chip" in c]
        m = re.search(r"(\d+) city-weeks · (\d+) OK · (\d+) Check · (\d+) RED", text_of(body))
        total, n_ok, n_check, n_red = map(int, m.groups())
        assert (total, n_ok, n_check, n_red) == (len(words), words.count("OK"), words.count("Check"),
                                                 words.count("RED")), (m.group(0), len(words))


def test_e02_attention_count_is_non_ok(ctx):
    for port in (ctx["port"], ctx["xport"]):
        body = get_ok(port, "/coverage")
        _weeks, cells = grid(body)
        non_ok = sum(1 for row in cells.values() for c in row.values() if "chip" in c and text_of(c) != "OK")
        assert len(attention_rows(body)) == non_ok, (len(attention_rows(body)), non_ok)


def test_e03_ordering_with_several_checks(ctx):
    rows = cov.coverage(ctx["xconn"])
    statuses = by_key(rows)
    assert statuses["A_HIGH", date(2026, 9, 7)]["status"] == "Check"
    assert statuses["A_HIGH", date(2026, 9, 14)]["status"] == "Check"
    assert statuses["B_HIGH", date(2026, 9, 7)]["status"] == "Check"
    expected = sorted((r for r in rows if r["status"] != "OK"), key=sort_key_expected)
    found = attention_rows(get_ok(ctx["xport"], "/coverage"))
    exp = [(r["status"], r["underlying_id"], str(r["week_start"])) for r in expected]
    got = [(f[2], f[0], f[1]) for f in found]
    assert got == exp, (got, exp)
    checks = [(f[0], f[1]) for f in found if f[2] == "Check"]
    assert checks == [("A_HIGH", "2026-09-07"), ("A_HIGH", "2026-09-14"), ("B_HIGH", "2026-09-07")], checks


def test_e04_gate_zero_vs_none(_ctx):
    base = {"underlying_id": "X", "series_ticker": "S", "station_id": "K", "week_start": date(2026, 9, 7),
            "events": 7, "markets": 7, "bars": 7, "nws_highs": 7, "kalshi_highs": 7,
            "numeric_mismatches": 0, "status": "Check"}
    zero = {**base, "gate_warnings": 0}
    none = {**base, "gate_warnings": None}
    two = {**base, "gate_warnings": 2}
    assert "gate 0 " in page_mod.cell_title(zero) and "gate – " in page_mod.cell_title(none)
    assert "gate 2 " in page_mod.cell_title(two)
    row_zero = attention_cells(page_mod.attention_section([zero]))
    row_none = attention_cells(page_mod.attention_section([none]))
    assert row_zero[8] == "0" and MISSING not in row_zero[8], row_zero
    assert row_none[8] == "—", row_none
    assert attention_cells(page_mod.attention_section([two]))[8] == "2"


def attention_cells(fragment):
    _tag, row = table_rows(fragment)[0]
    return [text_of(c) for _t, c in row]


def test_e05_every_cell_links(ctx):
    for port in (ctx["port"], ctx["xport"]):
        _weeks, cells = grid(get_ok(port, "/coverage"))
        for city, row in cells.items():
            for week, cell in row.items():
                if "chip" in cell:
                    href = html.unescape(re.search(r'href="([^"]*)"', cell).group(1))
                    assert href == f"/markets/{city}", (city, week, href)


def test_e06_parallel(ctx):
    expected = get_ok(ctx["port"], "/coverage")
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda _i: fetch(ctx["port"], "/coverage"), range(10)))
    assert all(s == 200 for s, _b in results), [s for s, _b in results]
    assert all(b == expected for _s, b in results), "parallel bodies differ"


def test_e07_live_time(ctx):
    start_t = time.perf_counter()
    get_ok(ctx["live_port"], "/coverage")
    elapsed = time.perf_counter() - start_t
    print(f"      live /coverage took {elapsed:.2f} s")
    assert elapsed < 2, f"{elapsed:.2f} s"


def test_e08_other_routes_unaffected(ctx):
    assert fetch(ctx["port"], "/coverage/")[0] == 404
    assert fetch(ctx["port"], "/coverage?x=1")[0] == 200
    body = get_ok(ctx["port"], "/runs")
    assert '<a href="/coverage">Coverage</a>' in body and 'aria-current' in body
    assert '<a href="/coverage">Coverage</a>' in get_ok(ctx["port"], "/markets")


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
        ctx = {"conn": conn, "xconn": xconn, "live_before": live_before}
        for key, name in (("port", DB), ("xport", XDB), ("live_port", "kalbt")):
            server, ctx[key] = start(name)
            servers.append(server)

        run_test("01 summary counts match the fixture; Coverage nav link is current", test_01_summary_and_nav, ctx)
        run_test("02 grid: one row per city, one column per week, chips, links, titles, missing", test_02_grid, ctx)
        run_test("03 Needs attention: RED before Check, numbers right-aligned and equal", test_03_attention, ctx)
        run_test("04 status_kind maps OK/Check/RED and rejects other", test_04_status_kind, ctx)
        run_test("05 empty result: 'No coverage data yet.' and no grid", test_05_empty, ctx)
        run_test("06 underlying_id with <>&\" is escaped", test_06_escaping, ctx)
        run_test("07 live: NYC_HIGH OK on 2026-11-16, run/intent/outcome counts unchanged", test_07_live, ctx)
        run_test("E01 summary counts equal chip words in the grid", test_e01_summary_equals_grid_chips, ctx)
        run_test("E02 Needs attention row count equals the non-OK cells", test_e02_attention_count_is_non_ok, ctx)
        run_test("E03 ordering RED, Check, city, week with several Check weeks", test_e03_ordering_with_several_checks, ctx)
        run_test("E04 gate_warnings 0 shows 0, None shows the missing mark", test_e04_gate_zero_vs_none, ctx)
        run_test("E05 every grid cell links to /markets/<city>", test_e05_every_cell_links, ctx)
        run_test("E06 10 parallel requests give identical 200 pages", test_e06_parallel, ctx)
        run_test("E07 live /coverage under 2 s", test_e07_live_time, ctx)
        run_test("E08 nav link on other pages; /coverage/ is 404", test_e08_other_routes_unaffected, ctx)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for c in conns:
            c.close()
        for name in (DB, XDB):
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
