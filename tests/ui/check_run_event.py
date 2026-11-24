"""Checks the run event page (spec C14, agreed tests 1 to 8).

Creates the throwaway database kalbt_ui_run_event_test_<pid> (schema + portfolio views + recon
fixture), builds the hand run of tests/ui/check_analytics.py plus one blocked intent on T-M3, serves
it with make_server on a free port and fetches pages over HTTP. The database is dropped first and at
the end (also when a test fails). Test 7 serves the live kalbt database and only reads (row counts
compared). Test 8 runs tests/ui/check_analytics.py unchanged.
Expected values are the hand values of the spec.
Run from the repo root: PYTHONPATH=. uv run python tests/ui/check_run_event.py
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
from tests.recon.check_check_run import (
    M1,
    M2,
    M3,
    D,
    at,
    filled,
    hand_run,
    load_fixture,
    refused,
)
from ui.serve import make_server

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = f"kalbt_ui_run_event_test_{os.getpid()}"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
LIVE_RUN = 32
EVENT = "EV-0601"

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


def text_rows(fragment):
    rows = []
    for row in re.findall(r"<tr[^>]*>.*?</tr>", fragment, flags=re.DOTALL):
        cells = re.findall(r"<td[^>]*>(.*?)</td>", row, flags=re.DOTALL)
        if cells:
            rows.append([text_of(c) for c in cells])
    return rows


def charts(body):
    """{market ticker: svg html} for every price chart on the page."""
    found = re.findall(r'<svg[^>]*aria-label="([^"]*)".*?</svg>', body, flags=re.DOTALL)
    svgs = re.findall(r"<svg[^>]*>.*?</svg>", body, flags=re.DOTALL)
    assert len(found) == len(svgs), (found, len(svgs))
    return dict(zip(found, svgs, strict=True))


def row_counts(conn):
    cur = conn.cursor()
    counts = {}
    for table in LIVE_TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


def build_run(conn):
    """The hand run of check_analytics.py plus a blocked intent on T-M3."""
    run_id = hand_run(
        conn,
        [(at(5, 31, 16, 0), M1, "yes", "buy", 5, [filled(5, "0.45", "0.09", at(5, 31, 16, 1))]),
         (at(6, 1, 2, 0), M2, "no", "buy", 10, [filled(10, "0.55", "0.18", at(6, 1, 2, 1))]),
         (at(6, 1, 12, 0), M3, "yes", "buy", 2, [refused("blocked", "locked", at(6, 1, 12, 1))])],
        snapshots=[(date(2026, 6, 1), M1, D(5), D("0.45")), (date(2026, 6, 1), M2, D(-10), D("0.45"))],
        settlements=[(M1, "yes", D(5), D(5)), (M2, "no", D(-10), D(10))])
    cur = conn.cursor()
    cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                " VALUES (%s, %s, NULL, 0.60, 0)", (M1, at(6, 1, 22, 0)))
    cur.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                " VALUES (%s, %s, 0.98, NULL, 0)", (M2, at(6, 2, 1, 30)))
    conn.commit()
    return run_id


def event_page(ctx):
    status, body = fetch(ctx["port"], f"/runs/{ctx['run']}/events/{EVENT}")
    assert status == 200, f"status {status}"
    return body


def circles(svg):
    """[(class, cx, cy, title)] of the fill circles of one chart."""
    found = re.findall(r'<circle class="([^"]*)" cx="([^"]*)" cy="([^"]*)" r="5"><title>(.*?)</title>', svg)
    return [(c, cx, cy, html.unescape(t)) for c, cx, cy, t in found]


# ---------------------------------------------------------------------------
# Agreed tests
# ---------------------------------------------------------------------------

def test_01_page(ctx):
    body = event_page(ctx)
    h1 = text_of(re.search(r"<h1>(.*?)</h1>", body, flags=re.DOTALL).group(1))
    assert EVENT in h1, h1
    assert f"run {ctx['run']}" in h1, h1


def test_02_two_charts(ctx):
    body = event_page(ctx)
    assert body.count("<svg") == 2, body.count("<svg")
    labels = sorted(charts(body))
    want = ["T-M1 YES bid and ask with fills", "T-M2 YES bid and ask with fills"]
    assert labels == want, labels


def test_03_fill_circles(ctx):
    found = charts(event_page(ctx))
    m1 = circles(found["T-M1 YES bid and ask with fills"])
    assert len(m1) == 1, m1
    assert m1[0][0] == "fill-buy", m1
    assert m1[0][3] == "2026-05-31 16:01 buy YES 5 @ 0.45", m1
    m2_svg = found["T-M2 YES bid and ask with fills"]
    m2 = circles(m2_svg)
    assert len(m2) == 1, m2
    assert m2[0][0] == "fill-buy", m2
    assert m2[0][3] == "2026-06-01 02:01 buy NO 10 @ 0.55", m2
    assert m2[0][2] == "130.8", f"cy {m2[0][2]}, expected 130.8 (YES 0.45)"


def test_04_orders_table(ctx):
    rows = text_rows(section(event_page(ctx), "Orders"))
    assert len(rows) == 3, rows
    assert [r[0] for r in rows] == ["2026-05-31 16:01", "2026-06-01 02:01", "2026-06-01 12:01"], rows
    assert [r[1] for r in rows] == ["T-M1", "T-M2", "T-M3"], rows
    last = rows[2]
    assert last[5] == "Blocked · locked", last
    assert last[6] == "—" and last[7] == "—", last


def test_05_404s(ctx):
    port = ctx["port"]
    status, _ = fetch(port, f"/runs/999999999/events/{EVENT}")
    assert status == 404, f"unknown run: {status}"
    status, _ = fetch(port, f"/runs/{ctx['empty']}/events/{EVENT}")
    assert status == 404, f"run without outcomes in the event: {status}"
    status, _ = fetch(port, f"/runs/{ctx['run']}/events/EV-0601x")
    assert status == 404, f"lowercase letter: {status}"


def test_06_analytics_link(ctx):
    status, body = fetch(ctx["port"], f"/runs/{ctx['run']}/analytics")
    assert status == 200, status
    assert f'href="/runs/{ctx["run"]}/events/{EVENT}"' in body, "no event link"


def test_07_live(ctx):
    live = connect()
    server = None
    try:
        cur = live.cursor()
        cur.execute("SELECT m.event_ticker FROM order_outcome o"
                    " JOIN order_intent i ON i.intent_id = o.intent_id"
                    " JOIN market m ON m.ticker = i.market_ticker"
                    " WHERE i.run_id = %s ORDER BY o.occurred_at, o.outcome_id LIMIT 1", (LIVE_RUN,))
        event = cur.fetchone()[0]
        live.rollback()
        before = row_counts(live)
        server, port = start("kalbt")
        began = time.monotonic()
        status, body = fetch(port, f"/runs/{LIVE_RUN}/events/{event}")
        elapsed = time.monotonic() - began
        assert status == 200, f"{event}: status {status}"
        assert elapsed < 5, f"took {elapsed:.1f} s"
        assert 'circle class="fill-buy"' in body, "no fill-buy circle"
        live.rollback()
        after = row_counts(live)
        assert after == before, f"live row counts: before {before}, after {after}"
    finally:
        if server is not None:
            server.shutdown()
            server.server_close()
        live.close()


def test_08_existing_suite(ctx):
    script = "tests/ui/check_analytics.py"
    result = subprocess.run([sys.executable, script], check=False, cwd=ROOT,
                            env={**os.environ, "PYTHONPATH": str(ROOT)},
                            capture_output=True, text=True, timeout=900)
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
        ctx = {"conn": conn, "port": port, "run": build_run(conn), "empty": hand_run(conn, [])}

        run_test("01 200, h1 has the event and the run id", test_01_page, ctx)
        run_test("02 two charts: markets with a filled outcome only", test_02_two_charts, ctx)
        run_test("03 fill circles: titles and NO fill drawn at 1 - p", test_03_fill_circles, ctx)
        run_test("04 orders table: 3 rows in time order, blocked row", test_04_orders_table, ctx)
        run_test("05 404: unknown run, run without outcomes, lowercase event", test_05_404s, ctx)
        run_test("06 analytics page links to the event page", test_06_analytics_link, ctx)
        run_test("07 live run 32: fast, read-only", test_07_live, ctx)
        run_test("08 check_analytics.py still passes", test_08_existing_suite, ctx)
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
