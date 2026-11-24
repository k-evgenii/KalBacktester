"""Checks the run analytics page (spec C13, agreed tests 1 to 10).

Creates the throwaway database kalbt_ui_analytics_test_<pid> (schema + portfolio views + recon
fixture), builds the same hand run as tests/portfolio/check_marks.py, serves it with make_server on
a free port and fetches pages over HTTP. The database is dropped first and at the end (also when a
test fails). Test 9 serves the live kalbt database and only reads (row counts compared).
Expected values are the hand values of the spec.
Run from the repo root: PYTHONPATH=. uv run python tests/ui/check_analytics.py
"""
import html
import http.client
import os
import re
import subprocess
import sys
import threading
import time
import traceback
from datetime import date
from pathlib import Path

from db.connect import connect
from tests.recon.check_check_run import M1, M2, D, at, filled, hand_run, load_fixture
from ui.serve import make_server

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = f"kalbt_ui_analytics_test_{os.getpid()}"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
LIVE_RUN = 32

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
    """(status, body text) for one GET."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=30)
    try:
        conn.request("GET", path)
        response = conn.getresponse()
        return response.status, response.read().decode("utf-8", errors="replace")
    finally:
        conn.close()


def start(dbname):
    server = make_server(0, dbname)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, server.server_address[1]


def text_of(cell):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", cell))).strip()


def section(body, heading):
    """The <section> html whose h2 starts with the given (escaped) text."""
    for found in re.findall(r"<section.*?</section>", body, flags=re.DOTALL):
        if re.search(rf"<h2>{re.escape(heading)}", found):
            return found
    raise AssertionError(f"no section {heading!r}")


def raw_rows(fragment):
    rows = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", fragment, flags=re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        if cells:
            rows.append(cells)
    return rows


def text_rows(fragment):
    return [[text_of(c) for c in row] for row in raw_rows(fragment)]


def tiles(body):
    """{label text: figure text}."""
    found = re.findall(r'<div class="tile[^"]*"><div class="tile-label">(.*?)</div>'
                       r'<div class="tile-figure">(.*?)</div></div>', body, flags=re.DOTALL)
    return {text_of(label): text_of(figure) for label, figure in found}


def row_counts(conn):
    cur = conn.cursor()
    counts = {}
    for table in LIVE_TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


def build_run(conn):
    """The hand run of tests/portfolio/check_marks.py."""
    run_id = hand_run(
        conn,
        [(at(5, 31, 16, 0), M1, "yes", "buy", 5, [filled(5, "0.45", "0.09", at(5, 31, 16, 1))]),
         (at(6, 1, 2, 0), M2, "no", "buy", 10, [filled(10, "0.55", "0.18", at(6, 1, 2, 1))])],
        snapshots=[(date(2026, 6, 1), M1, D(5), D("0.45")), (date(2026, 6, 1), M2, D(-10), D("0.45"))],
        settlements=[(M1, "yes", D(5), D(5)), (M2, "no", D(-10), D(10))])
    cur = conn.cursor()
    cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                " VALUES (%s, %s, NULL, 0.60, 0)", (M1, at(6, 1, 22, 0)))
    cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                " VALUES (%s, %s, 0.98, NULL, 0)", (M2, at(6, 2, 1, 30)))
    conn.commit()
    return run_id


def page_of(ctx):
    status, body = fetch(ctx["port"], f"/runs/{ctx['run']}/analytics")
    assert status == 200, f"status {status}"
    return body


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

def test_01_page(ctx):
    run_id = ctx["run"]
    body = page_of(ctx)
    title = re.search(r"<title>(.*?)</title>", body).group(1)
    assert "Analytics" in title, title
    h1 = re.search(r"<h1>(.*?)</h1>", body).group(1)
    assert f"run {run_id}" in text_of(h1), h1


def test_02_tiles(ctx):
    found = tiles(page_of(ctx))
    want = {"Net P&L": "+6.98", "Trades": "2", "Fees": "0.27", "Win rate": "100.0 %",
            "Profit factor": "—", "Return": "+0.70 %", "Peak capital at risk": "7.75",
            "Worst event loss": "7.75"}
    for label, figure in want.items():
        assert label in found, f"no tile {label!r}: {list(found)}"
        assert found[label] == figure, f"{label}: {found[label]!r}, expected {figure!r}"


def test_03_charts(ctx):
    run_id = ctx["run"]
    body = page_of(ctx)
    assert body.count("<svg") == 3, body.count("<svg")
    labels = re.findall(r'<svg[^>]*aria-label="([^"]*)"', body)
    assert len(labels) == 3, labels
    assert labels[0] == f"Equity of run {run_id}, hourly", labels
    assert labels[1] == f"Drawdown of run {run_id}, hourly", labels
    assert labels[2].startswith("P&amp;L by day"), labels
    equity = re.search(r"<svg[^>]*aria-label=\"Equity.*?</svg>", body, flags=re.DOTALL).group(0)
    end_label = re.search(r'<text class="chart-label"[^>]*>(.*?)</text>', equity).group(1)
    assert end_label == "1,006.98", end_label


def test_04_city_table(ctx):
    rows = text_rows(section(page_of(ctx), "P&amp;L by city"))
    assert rows == [["U1", "2", "2", "+6.98", "0.27"]], rows


def test_05_event_table(ctx):
    block = section(page_of(ctx), "P&amp;L by event")
    rows = text_rows(block)
    assert rows == [["2026-06-01", "U1", "EV-0601", "2", "+6.98", "7.75"]], rows
    assert 'href="/markets/U1/2026-06-01"' in block, block


def test_06_trades_table(ctx):
    rows = text_rows(section(page_of(ctx), "Trades"))
    by_market = {r[1]: r for r in rows}
    assert len(rows) == 2, rows
    assert by_market["T-M1"][0] == "2026-05-31 16:01", rows
    assert by_market["T-M1"][5:] == ["0.09", "+2.66", "Closed"], rows
    assert by_market["T-M2"][0] == "2026-06-01 02:01", rows
    assert by_market["T-M2"][5:] == ["0.18", "+4.32", "Closed"], rows


def test_07_unknown_and_no_fills(ctx):
    status, _ = fetch(ctx["port"], "/runs/999999999/analytics")
    assert status == 404, status
    empty = hand_run(ctx["conn"], [])
    status, body = fetch(ctx["port"], f"/runs/{empty}/analytics")
    assert status == 200, status
    assert "This run has no fills." in body, body[-500:]


def test_08_link(ctx):
    run_id = ctx["run"]
    status, body = fetch(ctx["port"], f"/runs/{run_id}")
    assert status == 200, status
    assert f'href="/runs/{run_id}/analytics"' in body, "no analytics link"


def test_09_live(ctx):
    live = connect()
    server = None
    try:
        before = row_counts(live)
        server, port = start("kalbt")
        began = time.monotonic()
        status, body = fetch(port, f"/runs/{LIVE_RUN}/analytics")
        elapsed = time.monotonic() - began
        assert status == 200, f"status {status}"
        assert elapsed < 5, f"took {elapsed:.1f} s"
        assert f"Equity of run {LIVE_RUN}" in body, "no equity chart"
        live.rollback()
        after = row_counts(live)
        assert after == before, f"live row counts: before {before}, after {after}"
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        live.close()


def test_10_existing_suites(ctx):
    for script in ("tests/ui/check_runs.py", "tests/ui/check_portfolio_page.py"):
        result = subprocess.run([sys.executable, script], check=False, cwd=ROOT,
                                env={**os.environ, "PYTHONPATH": str(ROOT)},
                                capture_output=True, text=True, timeout=600)
        assert result.returncode == 0, f"{script} exit {result.returncode}: {result.stdout[-800:]}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def main():
    admin = connect()
    admin.autocommit = True
    admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
    admin.execute(f"CREATE DATABASE {DB}")
    conn = None
    server = None
    try:
        conn = connect(dbname=DB)
        conn.execute(SCHEMA.read_text())
        conn.execute(PORTFOLIO.read_text())
        conn.commit()
        load_fixture(conn)
        server, port = start(DB)
        ctx = {"conn": conn, "port": port, "run": build_run(conn)}

        run_test("01 200, title Analytics, h1 has the run id", test_01_page, ctx)
        run_test("02 tiles: P&L, trades, fees, win rate, profit factor, return, risk", test_02_tiles, ctx)
        run_test("03 three svg charts, labels, equity end label", test_03_charts, ctx)
        run_test("04 city table", test_04_city_table, ctx)
        run_test("05 event table and date link", test_05_event_table, ctx)
        run_test("06 trades table", test_06_trades_table, ctx)
        run_test("07 unknown run 404; run with no fills", test_07_unknown_and_no_fills, ctx)
        run_test("08 run page links to analytics", test_08_link, ctx)
        run_test("09 live run 32: fast, read-only", test_09_live, ctx)
        run_test("10 check_runs.py and check_portfolio_page.py still pass", test_10_existing_suites, ctx)
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        if conn is not None:
            conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {DB} WITH (FORCE)")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
