"""Checks the portfolio page (spec C06, agreed tests 1 to 13, plus extra attacks E01 and up).

Creates throwaway databases kalbt_ui_portfolio_test (schema + portfolio views + recon fixture)
and kalbt_ui_portfolio_test_bare (schema + recon fixture, no portfolio views), serves them with
make_server on free ports, fetches pages over HTTP, and drops both databases first and at the
end (also when a test fails). Nothing is written to or changed in the live kalbt database; only
SELECT count(*) is run there (test 13). Expected numbers are read from the portfolio views of
the same throwaway database, plus hand values.
Run from the repo root: PYTHONPATH=. uv run python tests/ui/check_portfolio_page.py
"""
import html
import http.client
import os
import re
import subprocess
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb

from db.connect import connect
from tests.recon.check_check_run import clean_run, load_fixture
from ui.layout import count, money, price
from ui.portfolio import pnl, pnl_chart
from ui.serve import make_server

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = "kalbt_ui_portfolio_test"
BARE_DB = "kalbt_ui_portfolio_test_bare"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
D = Decimal

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
    """(status, lowercase headers, body text) for one raw request."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request(method, path)
        response = conn.getresponse()
        body = response.read().decode("utf-8", errors="replace")
        return response.status, {k.lower(): v for k, v in response.getheaders()}, body
    finally:
        conn.close()


def start(dbname):
    server = make_server(0, dbname)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def page_of(ctx, run_id):
    status, _, body = fetch(ctx["port"], f"/runs/{run_id}/portfolio")
    assert status == 200, f"status {status}"
    return body


def text_of(cell):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", cell))).strip()


def section(body, heading):
    """The <section> html whose h2 starts with the given (escaped) text."""
    for found in re.findall(r"<section.*?</section>", body, flags=re.DOTALL):
        if re.search(rf"<h2>{re.escape(heading)}", found):
            return found
    raise AssertionError(f"no section {heading!r}")


def raw_rows(fragment):
    """Body rows as lists of raw cell html (rows with at least one td)."""
    rows = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", fragment, flags=re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        if cells:
            rows.append(cells)
    return rows


def text_rows(fragment):
    return [[text_of(c) for c in row] for row in raw_rows(fragment)]


def tiles(body):
    """{label text: (tile css classes, figure html)}."""
    found = re.findall(r'<div class="(tile[^"]*)"><div class="tile-label">(.*?)</div>'
                       r'<div class="tile-figure">(.*?)</div></div>', body, flags=re.DOTALL)
    return {text_of(label): (css, figure) for css, label, figure in found}


def chart_points(body):
    found = re.search(r'<polyline class="chart-line" points="([^"]*)"', body)
    assert found, "no chart polyline"
    return [tuple(float(n) for n in p.split(",")) for p in found.group(1).split()]


def zero_line_y(svg):
    found = re.search(r'<line class="chart-zero"[^>]* y1="([-\d.]+)"', svg)
    assert found, "no zero line"
    return float(found.group(1))


def fmt_pnl(value):
    """Independent formatting of a P&L number: the sign, 2 decimals, a thousands separator."""
    if value > 0:
        return f"+{value:,.2f}"
    if value < 0:
        return f"-{-value:,.2f}"
    return "0.00"


def pnl_class(value):
    return "pnl-pos" if value > 0 else "pnl-neg" if value < 0 else "pnl-zero"


def view_daily(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT day, starting_cash, premium_paid, premium_received, fees, payouts, cash_end,"
                " cost_basis_open, realized_pnl_cum, fees_cum, net_pnl_cum, open_markets"
                " FROM portfolio_daily WHERE run_id = %s ORDER BY day", (run_id,))
    return cur.fetchall()


def view_positions(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT day, market_ticker, net_units, avg_cost, cost_basis"
                " FROM portfolio_position_daily WHERE run_id = %s ORDER BY day, market_ticker", (run_id,))
    return cur.fetchall()


def view_last_underlyings(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT underlying_id, cost_basis_open, realized_pnl_cum, fees_cum, net_pnl_cum,"
                " open_markets FROM portfolio_daily_underlying WHERE run_id = %s AND day ="
                " (SELECT max(day) FROM portfolio_daily WHERE run_id = %s) ORDER BY underlying_id",
                (run_id, run_id))
    return cur.fetchall()


def held_and_others(conn, run_id):
    rows = view_last_underlyings(conn, run_id)
    held = [r for r in rows if any(r[1:4]) or r[5]]
    return held, len(rows) - len(held)


def others_sentence(n):
    noun = "other underlying" if n == 1 else "other underlyings"
    return f"{n} {noun}: nothing held or traded (all 0)."


def at(month, day, hour=12, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=UTC)


def add_market(conn, ticker, event="EV-0601", close=None):
    conn.cursor().execute("INSERT INTO market (ticker, event_ticker, open_time, close_time)"
                          " VALUES (%s, %s, %s, %s)", (ticker, event, at(5, 30), close or at(6, 5)))
    conn.commit()


def add_underlying(conn, underlying_id, market, event="EV-X"):
    """A new underlying with its own series, event (2026-06-01) and one market."""
    cur = conn.cursor()
    cur.execute("INSERT INTO underlying (underlying_id, description, station_id)"
                " VALUES (%s, 'extra', 'KTST')", (underlying_id,))
    cur.execute("INSERT INTO series (ticker, underlying_id, title) VALUES (%s, %s, 'extra')",
                (f"S-{event}", underlying_id))
    cur.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                " VALUES (%s, %s, %s, '2026-06-01', 'NWS')", (event, f"S-{event}", underlying_id))
    conn.commit()
    add_market(conn, market, event)


def fill(market, side, action, quantity, when, fill_price, fee):
    return (market, side, action, quantity, when, ("filled", quantity, fill_price, fee, None))


def refuse(market, outcome, when, reason):
    return (market, "yes", "buy", 5, when, (outcome, 0, None, None, reason))


def make_run(conn, entries, start="2026-06-01", end="2026-06-02", cash=1000, strategy="hand"):
    """A run by hand. entries: fill(...) or refuse(...) tuples, each with one outcome."""
    cur = conn.cursor()
    cur.execute("INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end,"
                " status, finished_at) VALUES (%s, %s, 'test', %s, %s, %s, 'done', now())"
                " RETURNING run_id", (strategy, Jsonb({}), cash, start, end))
    run_id = cur.fetchone()[0]
    for market, side, action, quantity, when, (outcome, out_qty, out_price, fee, reason) in entries:
        cur.execute("INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity)"
                    " VALUES (%s, %s, %s, %s, %s, %s) RETURNING intent_id",
                    (run_id, when, market, side, action, quantity))
        intent_id = cur.fetchone()[0]
        cur.execute("INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee,"
                    " reason_code) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (intent_id, when, outcome, out_qty, out_price, fee, reason))
    conn.commit()
    return run_id


def live_counts():
    conn = connect()
    try:
        cur = conn.cursor()
        counts = {}
        for table in LIVE_TABLES:
            cur.execute(f"SELECT count(*) FROM {table}")
            counts[table] = cur.fetchone()[0]
        return counts
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

def test_01_tiles(ctx):
    run_id = ctx["clean"]
    body = page_of(ctx, run_id)
    last = view_daily(ctx["conn"], run_id)[-1]
    cash_end, cost_open, net_pnl, fees_cum = last[6], last[7], last[10], last[9]
    found = tiles(body)
    assert list(found) == ["Cash", "Open positions cost", "Net P&L after fees", "Fees"], list(found)
    assert text_of(found["Cash"][1]) == f"{cash_end:,.2f}", found["Cash"]
    assert text_of(found["Open positions cost"][1]) == f"{cost_open:,.2f}", found["Open positions cost"]
    assert text_of(found["Fees"][1]) == f"{fees_cum:,.2f}", found["Fees"]
    css, figure = found["Net P&L after fees"]
    assert text_of(figure) == fmt_pnl(net_pnl), (figure, net_pnl)
    assert f'class="pnl {pnl_class(net_pnl)}"' in figure, figure
    assert "tile-answer" in css, css
    assert all("tile-answer" not in found[k][0] for k in ("Cash", "Open positions cost", "Fees"))
    # hand check: fees are 0.09+0.21+0.14+0.06+0.06 = 0.56 on the clean run
    assert text_of(found["Fees"][1]) == "0.56", found["Fees"]
    summary = re.search(r'<p class="mono">(.*?)</p>', body, flags=re.DOTALL).group(1)
    days = view_daily(ctx["conn"], run_id)
    expected = (f"starting cash {days[0][1]:,.2f} · {days[0][0]} – {days[-1][0]} · {len(days)} days")
    assert text_of(summary) == expected, (text_of(summary), expected)
    assert body.index('class="tiles"') < body.index("<svg"), "tiles come before the chart"


def test_02_daily_book(ctx):
    run_id = ctx["clean"]
    body = page_of(ctx, run_id)
    days = view_daily(ctx["conn"], run_id)
    rows = text_rows(section(body, "Daily book"))
    assert len(rows) == len(days), (len(rows), len(days))
    assert [r[0] for r in rows] == [str(d[0]) for d in days], "days not in order"
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)
    for row, view in zip(rows, days, strict=True):
        assert len(row) == 11, row
        assert row[5] == f"{view[6]:,.2f}", (row, view[6])
        assert row[6] == f"{view[7]:,.2f}", (row, view[7])
        assert row[7] == fmt_pnl(view[8]), (row, view[8])
        assert row[8] == f"{view[9]:,.2f}", (row, view[9])
        assert row[9] == fmt_pnl(view[10]), (row, view[10])
        assert row[10] == count(view[11]), (row, view[11])
    heads = re.findall(r"<th[^>]*>(.*?)</th>", section(body, "Daily book"))
    assert [text_of(h) for h in heads] == ["Day", "Premium paid", "Premium received", "Fees", "Payouts",
                                          "Cash", "Open cost", "Realized P&L", "Fees to date",
                                          "Net P&L", "Open markets"], heads


def test_03_positions(ctx):
    run_id = ctx["clean"]
    body = page_of(ctx, run_id)
    view = view_positions(ctx["conn"], run_id)
    rows = text_rows(section(body, "Positions"))
    assert len(rows) == len(view) and len(view) > 0, (len(rows), len(view))
    for row, (day, market, net, avg, cost) in zip(rows, view, strict=True):
        side = "YES" if net > 0 else "NO"
        assert row == [str(day), market, side, count(abs(net)), price(avg), money(cost)], (row, view)
    m1 = [r for r in rows if r[0] == "2026-06-01" and r[1] == "T-M1"]
    assert m1 == [["2026-06-01", "T-M1", "YES", "10", "0.45", "4.50"]], m1


def test_04_by_underlying(ctx):
    conn = ctx["conn"]
    run_id = ctx["clean"]
    body = page_of(ctx, run_id)
    block = section(body, "By underlying")
    rows = text_rows(block)
    last = view_daily(conn, run_id)[-1]
    u1 = [r for r in rows if r[0] == "U1"]
    assert len(u1) == 1, rows
    assert u1[0] == ["U1", f"{last[7]:,.2f}", fmt_pnl(last[8]), f"{last[9]:,.2f}",
                     fmt_pnl(last[10]), count(last[11])], (u1, last)
    assert "other underlying" not in body, "sentence shown with 0 others"
    conn.cursor().execute("INSERT INTO underlying (underlying_id, description) VALUES ('U2', 'idle')")
    conn.commit()
    block = section(page_of(ctx, run_id), "By underlying")
    assert "<p>1 other underlying: nothing held or traded (all 0).</p>" in block, block
    assert "1 other underlyings" not in block
    assert [r[0] for r in text_rows(block)] == ["U1"], "U2 must not get a row"


def test_05_chart(ctx):
    run_id = ctx["clean"]
    body = page_of(ctx, run_id)
    days = view_daily(ctx["conn"], run_id)
    assert body.count('<svg class="chart"') == 1, body.count('<svg class="chart"')
    assert len(chart_points(body)) == len(days), (len(chart_points(body)), len(days))
    sentence = re.search(r"Net P&amp;L after fees ended at (.*?) on (\S+?)\.</p>", body)
    assert sentence, "no sentence"
    assert text_of(sentence.group(1)) == fmt_pnl(days[-1][10]), sentence.group(1)
    assert sentence.group(2) == str(days[-1][0]), sentence.group(2)


def test_06_pnl():
    assert pnl(D("45.48")) == '<span class="pnl pnl-pos">+45.48</span>', pnl(D("45.48"))
    neg = pnl(D("-2.62"))
    assert "pnl-neg" in neg and "-2.62" in neg, neg
    zero = pnl(D("0"))
    assert "pnl-zero" in zero and text_of(zero) == "0.00", zero
    assert "+1,045.50" in pnl(D("1045.5")), pnl(D("1045.5"))
    assert "—" in pnl(None), pnl(None)


def test_07_pnl_chart():
    one = pnl_chart([date(2026, 6, 1)], [D("3")])
    assert one.startswith("<svg"), one
    flat = pnl_chart([date(2026, 6, d) for d in (1, 2, 3)], [D(0), D(0), D(0)])
    assert flat.startswith("<svg"), flat
    two = pnl_chart([date(2026, 6, 1), date(2026, 6, 2)], [D(-1), D(2)])
    ys = [p[1] for p in chart_points(two)]
    zero = zero_line_y(two)
    assert min(ys) < zero < max(ys), (ys, zero)


def test_08_run_without_outcomes(ctx):
    conn = ctx["conn"]
    cur = conn.cursor()
    cur.execute("INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end,"
                " status, finished_at) VALUES ('empty', '{}', 'test', 1234.5, '2026-06-01', '2026-06-02',"
                " 'done', now()) RETURNING run_id")
    run_id = cur.fetchone()[0]
    conn.commit()
    body = page_of(ctx, run_id)
    assert text_of(tiles(body)["Cash"][1]) == "1,234.50", tiles(body)["Cash"]
    assert "No open positions." in body
    n = held_and_others(conn, run_id)[1]
    assert n >= 1
    assert others_sentence(n) in html.unescape(body), n
    assert len(chart_points(body)) == 2, chart_points(body)
    assert text_rows(section(body, "By underlying")) == [], "no table expected"


def test_09_404s(ctx):
    for path in ["/runs/999999/portfolio", "/runs/abc/portfolio"]:
        status, _, _ = fetch(ctx["port"], path)
        assert status == 404, f"{path} gave {status}"


def test_10_bare_database(ctx):
    status, _, body = fetch(ctx["bare_port"], f"/runs/{ctx['bare_clean']}/portfolio")
    assert status == 200, f"status {status}"
    assert "Portfolio views are not applied" in body, body[-500:]
    assert "Not available" in body and "chip-mute" in body


def test_11_link_and_c05(ctx):
    run_id = ctx["clean"]
    status, _, body = fetch(ctx["port"], f"/runs/{run_id}")
    assert status == 200, status
    assert f'href="/runs/{run_id}/portfolio"' in body, "no link"
    heading = re.search(r"<h2>Portfolio check.*?</h2>", body, flags=re.DOTALL)
    assert heading and f'href="/runs/{run_id}/portfolio"' in heading.group(0), "link not in the heading"
    env = {**os.environ, "PYTHONPATH": str(ROOT)}
    result = subprocess.run([sys.executable, str(ROOT / "tests" / "ui" / "check_runs.py")], cwd=ROOT,
                            env=env, capture_output=True, text=True, timeout=600, check=False)
    assert result.returncode == 0, result.stdout[-1500:] + result.stderr[-1500:]
    assert "[FAIL]" not in result.stdout


def test_12_escaping(ctx):
    conn = ctx["conn"]
    ugly = '<b>&"'
    add_market(conn, ugly)
    run_id = make_run(conn, [fill(ugly, "yes", "buy", 4, at(6, 1), D("0.30"), D("0.09"))])
    body = page_of(ctx, run_id)
    block = section(body, "Positions")
    assert ugly not in body, "raw ticker in page"
    assert "&lt;b&gt;&amp;&quot;" in block, block
    assert next(r[1] for r in text_rows(block)) == ugly


def test_13_live_untouched(ctx):
    after = live_counts()
    assert after == ctx["live_before"], (ctx["live_before"], after)


# ---------------------------------------------------------------------------
# Extra attacks
# ---------------------------------------------------------------------------

def test_e01_no_position(ctx):
    conn = ctx["conn"]
    add_market(conn, "T-NO")
    run_id = make_run(conn, [fill("T-NO", "no", "buy", 6, at(6, 1), D("0.30"), D("0.10"))])
    body = page_of(ctx, run_id)
    rows = text_rows(section(body, "Positions"))
    assert rows[0] == ["2026-06-01", "T-NO", "NO", "6", "0.30", "1.80"], rows
    view = view_positions(conn, run_id)
    assert view[0][2] < 0 and view[0][3] == D("0.3000"), view[0]
    assert text_of(tiles(body)["Open positions cost"][1]) == "1.80"
    assert text_of(tiles(body)["Cash"][1]) == "998.10", tiles(body)["Cash"]


def test_e02_negative_then_positive(ctx):
    conn = ctx["conn"]
    add_market(conn, "T-NP")
    run_id = make_run(conn, [fill("T-NP", "yes", "buy", 10, at(6, 1), D("0.50"), D("0.18")),
                             fill("T-NP", "yes", "sell", 10, at(6, 2), D("0.60"), D("0.17"))],
                      "2026-06-01", "2026-06-03")
    body = page_of(ctx, run_id)
    raw = raw_rows(section(body, "Daily book"))
    nets = [r[9] for r in raw]
    assert [text_of(n) for n in nets] == ["-0.18", "+0.65", "+0.65"], nets
    assert ["pnl-neg" in n for n in nets] == [True, False, False]
    assert ["pnl-pos" in n for n in nets] == [False, True, True]
    assert [text_of(r[7]) for r in raw] == ["0.00", "+1.00", "+1.00"], "realized column"
    assert "pnl-zero" in raw[0][7]
    assert 'class="pnl pnl-pos">+0.65</span>' in re.search(r"Net P&amp;L after fees ended.*", body).group(0)
    ys = [p[1] for p in chart_points(body)]
    zero = zero_line_y(body)
    assert ys[0] > zero > ys[1], (ys, zero)  # first below the zero line, second above
    assert len(ys) == 3


def test_e03_fractional_units(ctx):
    conn = ctx["conn"]
    add_market(conn, "T-FR")
    run_id = make_run(conn, [fill("T-FR", "yes", "buy", D("2.5"), at(6, 1), D("0.40"), D("0.05"))])
    rows = text_rows(section(page_of(ctx, run_id), "Positions"))
    assert rows[0][3] == "2.5", rows[0]
    assert rows[0][4:] == ["0.40", "1.00"], rows[0]


def test_e04_thirty_days(ctx):
    conn = ctx["conn"]
    add_market(conn, "T-30")
    run_id = make_run(conn, [fill("T-30", "yes", "buy", 3, at(6, 1), D("0.40"), D("0.09")),
                             fill("T-30", "yes", "sell", 3, at(6, 15), D("0.70"), D("0.06"))],
                      "2026-06-01", "2026-06-30")
    body = page_of(ctx, run_id)
    assert len(view_daily(conn, run_id)) == 30
    points = chart_points(body)
    assert len(points) == 30, len(points)
    svg = re.search(r"<svg.*?</svg>", body, flags=re.DOTALL).group(0)
    numbers = [(float(a), float(b)) for a, b in re.findall(r'\bc?x\d?="([-\d.]+)"[^>]*?\bc?y\d?="([-\d.]+)"', svg)]
    all_xy = points + numbers
    assert all(0 <= x <= 640 and 0 <= y <= 160 for x, y in all_xy), [p for p in all_xy if not (0 <= p[0] <= 640 and 0 <= p[1] <= 160)]
    label = re.search(r'<text class="chart-label" x="([-\d.]+)" y="([-\d.]+)">(.*?)</text>', svg)
    assert 0 <= float(label.group(1)) and float(label.group(1)) + 70 <= 640, label.groups()
    assert 0 <= float(label.group(2)) <= 160
    assert len(text_rows(section(body, "Daily book"))) == 30
    assert len(text_rows(section(body, "Positions"))) == len(view_positions(conn, run_id)) == 14


def test_e05_chart_extremes():
    for values in ([D("-5000.5"), D("-1"), D("-0.01")], [D("1000000"), D("-1000000")],
                   [D(0), D(0)], [D("0.01")] * 40, [D("-3")]):
        days = [date(2026, 6, 1)] * len(values)
        svg = pnl_chart(days, values)
        coords = [float(n) for n in re.findall(r'"([-\d.]+)"', re.sub(r'aria-label="[^"]*"|viewBox="[^"]*"', "", svg))]
        assert coords and all(0 <= c <= 640 for c in coords), (values, coords)
        pts = chart_points(svg)
        assert all(0 <= y <= 160 and 0 <= x <= 640 for x, y in pts), (values, pts)
        assert 0 <= zero_line_y(svg) <= 160


def test_e06_only_rejected_blocked(ctx):
    conn = ctx["conn"]
    add_market(conn, "T-RJ")
    run_id = make_run(conn, [refuse("T-RJ", "rejected", at(6, 1), "volume_cap"),
                             refuse("T-RJ", "blocked", at(6, 1, 15), "after_daily_high")],
                      "2026-06-01", "2026-06-03")
    body = page_of(ctx, run_id)
    found = tiles(body)
    assert text_of(found["Cash"][1]) == "1,000.00"
    assert text_of(found["Net P&L after fees"][1]) == "0.00" and "pnl-zero" in found["Net P&L after fees"][1]
    assert text_of(found["Fees"][1]) == "0.00"
    assert "No open positions." in body
    assert len(chart_points(body)) == 3
    assert "ended at" in body and "0.00" in text_of(re.search(r"ended at.*?</p>", body).group(0))


def test_e07_other_underlyings_wording(ctx):
    conn = ctx["conn"]
    run_id = ctx["clean"]
    # U1 is held; U2 was added by test 4. Reach exactly 0, 1 and 2 others on a separate run.
    others = held_and_others(conn, run_id)[1]
    assert others == 1, others  # U2 only so far
    conn.cursor().execute("INSERT INTO underlying (underlying_id, description) VALUES ('U3', 'idle')")
    conn.commit()
    block = section(page_of(ctx, run_id), "By underlying")
    assert "<p>2 other underlyings: nothing held or traded (all 0).</p>" in block, block
    assert [r[0] for r in text_rows(block)] == ["U1"]


def test_e08_zero_others_in_isolation(ctx):
    """In a database where every underlying is held: no sentence at all."""
    conn = ctx["iso_conn"]
    run_id = ctx["iso_run"]
    body = fetch(ctx["iso_port"], f"/runs/{run_id}/portfolio")[2]
    held, others = held_and_others(conn, run_id)
    assert others == 0 and len(held) == 1, (held, others)
    assert "other underlying" not in body
    assert [r[0] for r in text_rows(section(body, "By underlying"))] == ["U1"]


def test_e09_underlying_escaping(ctx):
    conn = ctx["conn"]
    ugly = 'U<>&"'
    add_underlying(conn, ugly, "T-UGLY", "EV-UGLY")
    run_id = make_run(conn, [fill("T-UGLY", "yes", "buy", 2, at(6, 1), D("0.50"), D("0.04"))])
    body = page_of(ctx, run_id)
    block = section(body, "By underlying")
    assert ugly not in body, "raw underlying in page"
    assert "U&lt;&gt;&amp;&quot;" in block, block
    assert next(r[0] for r in text_rows(block)) == ugly


def test_e10_bare_unknown_run(ctx):
    status, _, body = fetch(ctx["bare_port"], "/runs/999999/portfolio")
    assert status == 404, f"unknown run on a database without views gave {status}"
    assert "Portfolio views are not applied" not in body


def test_e11_parallel(ctx):
    path = f"/runs/{ctx['clean']}/portfolio"
    with ThreadPoolExecutor(10) as pool:
        results = list(pool.map(lambda _: fetch(ctx["port"], path), range(10)))
    assert [r[0] for r in results] == [200] * 10, [r[0] for r in results]
    assert len({r[2] for r in results}) == 1, "bodies differ"


def test_e12_page_shell(ctx):
    run_id = ctx["clean"]
    headers, body = fetch(ctx["port"], f"/runs/{run_id}/portfolio")[1:]
    assert headers["content-type"].startswith("text/html")
    assert body.startswith("<!doctype html>")
    assert f"<title>Portfolio · run {run_id} · KalBacktester</title>" in body
    assert f'<a href="/runs/{run_id}">' in body, "no back link"
    assert 'aria-current="page"' in body
    order = [body.index(s) for s in ('class="tiles"', "<svg", "<h2>Daily book", "<h2>Positions",
                                     "<h2>By underlying")]
    assert order == sorted(order), order
    assert re.search(r'(src|href)="https?://', body) is None, "external URL"


def test_e13_odd_paths(ctx):
    for path in ["/runs/0/portfolio", "/runs/1/portfolio/", "/runs//portfolio", "/runs/-1/portfolio",
                 "/runs/1x/portfolio", "/runs/" + "9" * 19 + "/portfolio", "/runs/portfolio"]:
        status, _, _ = fetch(ctx["port"], path)
        assert status == 404, f"{path} gave {status}"
    status, _, _ = fetch(ctx["port"], f"/runs/{ctx['clean']}/portfolio?x=1")
    assert status == 200, status
    status, _, _ = fetch(ctx["port"], f"/runs/{ctx['clean']}/portfolio", "POST")
    assert status == 405, status


def test_e14_css_rules(ctx):
    status, _, css = fetch(ctx["port"], "/static/app.css")
    assert status == 200
    for rule in [".pnl-pos", ".pnl-neg", ".pnl-zero", ".chart-zero", ".chart-line", ".chart-end",
                 ".chart-label", ".tiles", ".tile-answer", ".tile-label", ".tile-figure",
                 "minmax(190px, 1fr)", "32px", "22px"]:
        assert rule in css, f"{rule} missing from app.css"


def test_e15_pnl_edges():
    assert "+1,000,000.00" in pnl(D("1000000"))
    assert "-1,234.57" in pnl(D("-1234.567"))
    assert "pnl-zero" in pnl(D("0.00"))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def make_db(admin, name, with_portfolio):
    admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {name}")
    conn = connect(name)
    conn.execute(SCHEMA.read_text())
    if with_portfolio:
        conn.execute(PORTFOLIO.read_text())
    conn.commit()
    load_fixture(conn)
    return conn


def main():
    live_before = live_counts()
    admin = connect()
    admin.autocommit = True
    iso_name = DB + "_iso"
    names = (DB, BARE_DB, iso_name)
    conns, servers = [], []
    try:
        for name in names:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        conn = make_db(admin, DB, True)
        conns.append(conn)
        bare_conn = make_db(admin, BARE_DB, False)
        conns.append(bare_conn)
        iso_conn = make_db(admin, iso_name, True)
        conns.append(iso_conn)

        server, port = start(DB)
        bare_server, bare_port = start(BARE_DB)
        iso_server, iso_port = start(iso_name)
        servers += [server, bare_server, iso_server]

        ctx = {"conn": conn, "port": port, "bare_port": bare_port, "live_before": live_before,
               "iso_conn": iso_conn, "iso_port": iso_port}
        ctx["clean"] = clean_run(conn)
        conn.commit()
        ctx["bare_clean"] = clean_run(bare_conn)
        bare_conn.commit()
        ctx["iso_run"] = clean_run(iso_conn)
        iso_conn.commit()

        run_test("01 tiles show the last day's cash, open cost, net P&L and fees", test_01_tiles, ctx)
        run_test("02 daily book: a row per day, in order, Cash matches", test_02_daily_book, ctx)
        run_test("03 positions: a row per view row, M1 YES 10 at 0.45", test_03_positions, ctx)
        run_test("04 by underlying: U1 row equals last day; 1 other underlying", test_04_by_underlying, ctx)
        run_test("05 chart: one svg, a point per day, sentence names last day", test_05_chart, ctx)
        run_test("06 pnl formatting", lambda _: test_06_pnl(), ctx)
        run_test("07 pnl_chart one day, flat, zero line between", lambda _: test_07_pnl_chart(), ctx)
        run_test("08 run with no outcomes", test_08_run_without_outcomes, ctx)
        run_test("09 unknown and non-numeric run give 404", test_09_404s, ctx)
        run_test("10 database without views: not applied page", test_10_bare_database, ctx)
        run_test("11 run page links here; check_runs.py still passes", test_11_link_and_c05, ctx)
        run_test("12 market ticker with <b>&\" is escaped in positions", test_12_escaping, ctx)
        run_test("E01 NO position: side NO, avg cost is the NO price", test_e01_no_position, ctx)
        run_test("E02 net P&L negative then positive: classes and zero line", test_e02_negative_then_positive, ctx)
        run_test("E03 fractional units 2.5", test_e03_fractional_units, ctx)
        run_test("E04 30 calendar days: 30 points, inside the viewBox", test_e04_thirty_days, ctx)
        run_test("E05 pnl_chart extremes stay inside the viewBox", lambda _: test_e05_chart_extremes(), ctx)
        run_test("E06 only rejected/blocked events", test_e06_only_rejected_blocked, ctx)
        run_test("E07 '2 other underlyings' wording", test_e07_other_underlyings_wording, ctx)
        run_test("E08 0 others: no sentence", test_e08_zero_others_in_isolation, ctx)
        run_test("E09 underlying id with <>&\" is escaped", test_e09_underlying_escaping, ctx)
        run_test("E10 unknown run on a database without views is 404", test_e10_bare_unknown_run, ctx)
        run_test("E11 10 parallel requests all 200 and equal", test_e11_parallel, ctx)
        run_test("E12 page shell, title, back link, section order", test_e12_page_shell, ctx)
        run_test("E13 odd paths 404, query string ok, POST 405", test_e13_odd_paths, ctx)
        run_test("E14 app.css has the new rules", test_e14_css_rules, ctx)
        run_test("E15 pnl large numbers and zero", lambda _: test_e15_pnl_edges(), ctx)
        run_test("13 live kalbt row counts unchanged", test_13_live_untouched, ctx)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for conn in conns:
            conn.close()
        for name in names:
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
