"""Checks the runs UI (spec C05, agreed tests 1 to 13, plus extra attacks E1 and up).

Creates throwaway databases kalbt_ui_test (schema + portfolio views + recon fixture) and
kalbt_ui_test_bare (schema + recon fixture, no portfolio views), serves them with make_server
on free ports, fetches pages over HTTP, and drops both databases at the end. Nothing is written
to or changed in the live kalbt database; only SELECT count(*) is run there (test 13).
Run from the repo root: PYTHONPATH=. uv run python tests/ui/check_runs.py
"""
import html
import http.client
import re
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from db.connect import connect
from portfolio.check_run import check_run as portfolio_check
from tests.recon.check_check_run import clean_run, load_fixture, tamper
from ui.layout import chip, count, money, price
from ui.serve import make_server

ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = ROOT / "db" / "schema.sql"
PORTFOLIO = ROOT / "db" / "portfolio.sql"
DB = "kalbt_ui_test"
BARE_DB = "kalbt_ui_test_bare"
LIVE_TABLES = ["run", "order_intent", "order_outcome"]
UGLY = '<img src=x onerror=alert(1)> & "q" \'s\''
UGLY_ESCAPED = html.escape(UGLY)

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
    """(status, lowercase headers, body text) for one raw request, no redirect following."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
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


def text_of(cell):
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]*>", "", cell))).strip()


def table_rows(fragment, row_class=None):
    """Rows of a fragment as lists of cell texts (tr with the class, or all tr)."""
    pattern = r"<tr[^>]*>.*?</tr>"
    rows = []
    for row in re.findall(pattern, fragment, flags=re.DOTALL):
        if row_class and f'class="{row_class}"' not in row:
            continue
        rows.append([text_of(c) for c in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, flags=re.DOTALL)])
    return rows


def section(body, heading):
    """The <section> html that starts with the given h2 text."""
    for found in re.findall(r"<section.*?</section>", body, flags=re.DOTALL):
        if re.search(rf"<h2>{heading}", found):
            return found
    raise AssertionError(f"no section {heading!r}")


def journal(body):
    found = re.search(r'<table id=journal>.*?</table>', body, flags=re.DOTALL)
    assert found, "no <table id=journal>"
    return found.group(0)


def outcome_counts(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT o.outcome, count(*) FROM order_outcome o JOIN order_intent i USING (intent_id)"
                " WHERE i.run_id = %s GROUP BY 1", (run_id,))
    found = dict(cur.fetchall())
    return found.get("filled", 0), found.get("rejected", 0), found.get("blocked", 0)


def insert_run(conn, strategy, params=None, entries=()):
    """A run inserted by hand. entries: (market, decided_at, [(outcome, qty, price, fee, reason)])."""
    cur = conn.cursor()
    cur.execute("INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end,"
                " status, finished_at) VALUES (%s, %s, 'test', 1000, '2026-05-31', '2026-06-02',"
                " 'done', now()) RETURNING run_id", (strategy, Jsonb(params or {})))
    run_id = cur.fetchone()[0]
    for market, decided_at, outcomes in entries:
        cur.execute("INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity)"
                    " VALUES (%s, %s, %s, 'yes', 'buy', 5) RETURNING intent_id",
                    (run_id, decided_at, market))
        intent_id = cur.fetchone()[0]
        for outcome, quantity, fill_price, fee, reason in outcomes:
            cur.execute("INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee,"
                        " reason_code) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (intent_id, decided_at, outcome, quantity, fill_price, fee, reason))
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

def test_01_root_redirect(ctx):
    status, headers, _ = fetch(ctx["port"], "/")
    assert status == 302, f"status {status}"
    assert headers.get("location") == "/runs", f"Location {headers.get('location')}"


def test_02_runs_list(ctx):
    status, headers, body = fetch(ctx["port"], "/runs")
    assert status == 200, f"status {status}"
    assert headers["content-type"].startswith("text/html"), headers["content-type"]
    run_id = ctx["clean"]
    assert f'href="/runs/{run_id}"' in body, "no link to the run"
    rows = [r for r in re.findall(r"<tr[^>]*>.*?</tr>", body, flags=re.DOTALL) if f'href="/runs/{run_id}"' in r]
    assert len(rows) == 1, f"{len(rows)} rows for the run"
    cells = [text_of(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", rows[0], flags=re.DOTALL)]
    filled, rejected, blocked = outcome_counts(ctx["conn"], run_id)
    assert cells[0] == str(run_id), cells
    assert cells[1] == "recon", cells
    assert cells[3] == "Done", cells
    assert cells[4] == "7", cells
    assert [cells[5], cells[6], cells[7]] == [str(filled), str(rejected), str(blocked)], cells
    assert (filled, rejected, blocked) == (5, 2, 1), (filled, rejected, blocked)


def test_03_run_page(ctx):
    run_id = ctx["clean"]
    status, _, body = fetch(ctx["port"], f"/runs/{run_id}")
    assert status == 200, f"status {status}"
    recon = section(body, "Reconciliation")
    assert "PASS" in recon, "no PASS verdict"
    assert recon.count('class="check-row"') == 13, f"{recon.count('class=\"check-row\"')} check rows"
    portfolio = section(body, "Portfolio check")
    rows = table_rows(portfolio)[1:]
    assert [r[0] for r in rows] == ["fees", "snapshots", "settlements"], rows
    assert all(r[1] == "OK" for r in rows), rows
    cur = ctx["conn"].cursor()
    cur.execute("SELECT count(*) FROM order_outcome o JOIN order_intent i USING (intent_id)"
                " WHERE i.run_id = %s", (run_id,))
    expected = cur.fetchone()[0]
    table = journal(body)
    assert table.count('class="journal-row"') == expected, \
        f"{table.count('class=\"journal-row\"')} journal rows, expected {expected}"
    assert table.count("<tr") == expected + 1, "tr count in journal"


def test_04_journal_contents(ctx):
    _, _, body = fetch(ctx["port"], f"/runs/{ctx['clean']}")
    rows = table_rows(journal(body), "journal-row")
    # Decided, Market, Side, Action, Requested, Outcome, Qty, Price, Fee, Reason
    m1_fills = [r for r in rows if r[1] == "T-M1" and r[5] == "Filled"]
    assert m1_fills, rows
    first = m1_fills[0]
    assert first[0] == "05-31 16:00", first
    assert first[7] == "0.45" and first[8] == "0.09", first
    nothing = [r for r in rows if r[5] == "Rejected" and r[9] == "nothing_to_sell"]
    assert len(nothing) == 1, rows
    lock = [r for r in rows if r[5] == "Blocked"]
    assert len(lock) == 1 and lock[0][9] == "after_daily_high", lock


def test_05_404s(ctx):
    for path in ["/runs/999999", "/runs/abc", "/nope"]:
        status, _, _ = fetch(ctx["port"], path)
        assert status == 404, f"{path} gave {status}"


def test_06_static(ctx):
    port = ctx["port"]
    status, headers, _ = fetch(port, "/static/app.css")
    assert status == 200 and headers["content-type"] == "text/css; charset=utf-8", (status, headers)
    status, headers, _ = fetch(port, "/static/fonts/IBMPlexSans-Regular-Latin1.woff2")
    assert status == 200 and headers["content-type"] == "font/woff2", (status, headers)
    for path in ["/static/../serve.py", "/static/%2e%2e/serve.py", "/static/VENDORED.md"]:
        status, _, _ = fetch(port, path)
        assert status == 404, f"{path} gave {status}"


def test_07_post(ctx):
    status, _, _ = fetch(ctx["port"], "/runs", "POST")
    assert status == 405, f"status {status}"


def test_08_escaping(ctx):
    evil = "<script>alert(1)</script>"
    run_id = insert_run(ctx["conn"], evil)
    for path in ["/runs", f"/runs/{run_id}"]:
        status, _, body = fetch(ctx["port"], path)
        assert status == 200, f"{path} status {status}"
        assert "<script>alert" not in body, f"raw script on {path}"
        assert html.escape(evil) in body, f"escaped text missing on {path}"


def test_09_read_only(ctx):
    conn = connect(DB)
    try:
        conn.read_only = True
        try:
            conn.execute("INSERT INTO underlying (underlying_id, description) VALUES ('X', 'x')")
        except psycopg.errors.ReadOnlySqlTransaction:
            return
        raise AssertionError("insert was accepted on a read-only connection")
    finally:
        conn.close()


def test_10_bare_database(ctx):
    status, _, body = fetch(ctx["bare_port"], f"/runs/{ctx['bare_clean']}")
    assert status == 200, f"status {status}"
    assert section(body, "Reconciliation").count('class="check-row"') == 13
    assert "Portfolio views are not applied" in body, "no 'not applied' text"
    assert "Not available" in body


def test_11_formatters(ctx):
    assert money(Decimal("1045.48")) == "1,045.48"
    assert money(Decimal(0)) == "0.00"
    assert price(Decimal("0.2300")) == "0.23"
    assert price(Decimal("0.2345")) == "0.2345"
    assert count(Decimal("2.5")) == "2.5"
    assert count(3) == "3"
    assert "—" in money(None)
    assert "chip-ok" in chip("OK", "ok") and "OK" in chip("OK", "ok")
    try:
        chip("x", "pink")
    except ValueError:
        return
    raise AssertionError("chip with unknown kind did not raise")


def test_12_full_documents(ctx):
    paths = ["/runs", f"/runs/{ctx['clean']}", "/runs/999999", "/nope"]
    pages = [(p, fetch(ctx["port"], p)[2]) for p in paths]
    pages.append(("bare run page", fetch(ctx["bare_port"], f"/runs/{ctx['bare_clean']}")[2]))
    for path, body in pages:
        assert body.startswith("<!doctype html>"), f"{path} starts {body[:30]!r}"
        assert 'href="/static/app.css"' in body, f"{path} no css link"
        assert 'id="theme-toggle"' in body, f"{path} no toggle"
        assert "http://" not in body and "https://" not in body, f"{path} has an absolute URL"


def test_13_live_untouched(ctx):
    after = live_counts()
    assert after == ctx["live_before"], f"before {ctx['live_before']} after {after}"


# ---------------------------------------------------------------------------
# Extra attacks
# ---------------------------------------------------------------------------

def test_e01_static_path_tricks(ctx):
    paths = [
        "/static/..%2fserve.py", "/static/%2e%2e%2fserve.py", "/static/fonts/../../serve.py",
        "/static/%2e%2e%2f%2e%2e%2fdb/schema.sql", "/static/%2fetc%2fpasswd", "/static//etc/passwd",
        "/static/fonts/../../../etc/passwd", "/static/fonts/", "/static/fonts", "/static/", "/static",
        "/static/%00", "/static/app.css%00.png", "/static/nope.css",
        "/static/..", "/static/.", "/static/%5c..%5cserve.py", "/static/fonts/%2e%2e/%2e%2e/serve.py",
    ]
    bad = []
    for path in paths:
        status, _, body = fetch(ctx["port"], path)
        if status != 404 or "def make_server" in body:
            bad.append((path, status))
    assert not bad, f"not 404: {bad}"


def test_e01b_double_slash_prefix(ctx):
    status, _, _ = fetch(ctx["port"], "//static/app.css")
    assert status in (200, 404), f"status {status}"
    status, _, body = fetch(ctx["port"], "//static/../serve.py")
    assert status == 404 and "def make_server" not in body, status


def test_e02_static_ok_files(ctx):
    status, headers, _ = fetch(ctx["port"], "/static/htmx.min.js")
    assert status == 200 and headers["content-type"] == "text/javascript", (status, headers)
    for name in ["IBMPlexSans-SemiBold-Latin1.woff2", "IBMPlexSans-Bold-Latin1.woff2",
                 "IBMPlexMono-Regular-Latin1.woff2"]:
        status, headers, _ = fetch(ctx["port"], f"/static/fonts/{name}")
        assert status == 200 and headers["content-type"] == "font/woff2", (name, status)


def test_e03_odd_run_ids(ctx):
    port = ctx["port"]
    for path in ["/runs/-1", "/runs/0", "/runs/1.5", "/runs/%31", "/runs/1/", "/runs/", "/runs//1",
                 "/runs/1%20", "/runs/%D9%A1"]:
        status, _, _ = fetch(port, path)
        assert status == 404, f"{path[:40]} gave {status}, expected 404"


def test_e04_very_long_digit_id_is_404(ctx):
    for path in ["/runs/99999999999999999999999", "/runs/9223372036854775808"]:
        status, _, body = fetch(ctx["port"], path)
        assert status == 404, f"{path} gave {status} (expected 404 for an id that cannot exist): {body[-300:]}"


def test_e04b_five_thousand_digit_id_is_404(ctx):
    status, _, body = fetch(ctx["port"], "/runs/" + "9" * 5000)
    assert status == 404, f"5000-digit id gave {status}: {body[-300:]}"


def test_e04c_digit_count_boundary(ctx):
    for digits in (17, 18, 19, 20):
        status, _, _ = fetch(ctx["port"], "/runs/" + "9" * digits)
        assert status == 404, f"{digits} digits gave {status}"
    status, _, _ = fetch(ctx["port"], "/runs/" + "0" * 30 + str(ctx["clean"]))
    assert status in (200, 404), f"zero-padded long id gave {status}"
    status, _, _ = fetch(ctx["port"], "/runs/" + "1" + "0" * 17)
    assert status == 404, f"18-digit nonexistent id gave {status}"
    for path in ["/runs/%00", f"/runs/{ctx['clean']}%00", "/runs%00", "/static/fonts/%00/x"]:
        status, _, _ = fetch(ctx["port"], path)
        assert status == 404, f"{path} gave {status}"
    status, _, _ = fetch(ctx["port"], "/runs")
    assert status == 200, "server broken after the attacks"


def test_e05_leading_zeros(ctx):
    padded = f"{ctx['clean']:05d}"
    status, _, _ = fetch(ctx["port"], f"/runs/{padded}")
    assert status in (200, 404), f"status {status}"


def test_e06_query_strings(ctx):
    for path in ["/runs?x=1", f"/runs/{ctx['clean']}?y", f"/runs/{ctx['clean']}?y=1&z=%3Cscript%3E",
                 "/?a=b", "/static/app.css?v=3"]:
        status, _, _ = fetch(ctx["port"], path)
        expected = 302 if path.startswith("/?") else 200
        assert status == expected, f"{path} gave {status}"
    status, _, _ = fetch(ctx["port"], "/nope?x=1")
    assert status == 404


def test_e07_other_methods(ctx):
    for method in ["HEAD", "PUT", "DELETE", "PATCH"]:
        for path in ["/runs", "/", "/static/app.css"]:
            status, _, _ = fetch(ctx["port"], path, method)
            assert status == 405, f"{method} {path} gave {status}"
    status, _, _ = fetch(ctx["port"], "/runs", "OPTIONS")
    assert status in (405, 501), f"OPTIONS gave {status}"
    status, _, _ = fetch(ctx["port"], "/runs", "BREW")
    assert status in (405, 501), f"BREW gave {status}"


def test_e08_run_without_intents(ctx):
    run_id = insert_run(ctx["conn"], "empty-run")
    status, _, body = fetch(ctx["port"], f"/runs/{run_id}")
    assert status == 200, f"status {status}"
    assert journal(body).count('class="journal-row"') == 0
    status, _, body = fetch(ctx["port"], "/runs")
    rows = [r for r in re.findall(r"<tr[^>]*>.*?</tr>", body, flags=re.DOTALL) if f'href="/runs/{run_id}"' in r]
    cells = [text_of(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", rows[0], flags=re.DOTALL)]
    assert cells[4:8] == ["0", "0", "0", "0"], cells


def test_e09_intent_without_outcome(ctx):
    when = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    run_id = insert_run(ctx["conn"], "no-outcome", entries=[("T-M1", when, [])])
    status, _, body = fetch(ctx["port"], f"/runs/{run_id}")
    assert status == 200, f"status {status}"
    rows = table_rows(journal(body), "journal-row")
    assert len(rows) == 1, rows
    row = rows[0]
    assert row[5] == "No outcome", row
    assert row[6] == row[7] == row[8] == row[9] == "—", row
    assert "chip-red" in re.search(r'<tr class="journal-row">.*?</tr>', journal(body), flags=re.DOTALL).group(0)


def test_e10_failing_checks(ctx):
    conn = ctx["conn"]
    run_id = clean_run(conn)
    tamper(conn, "position_snapshot",
           "UPDATE position_snapshot SET net_units = net_units + 1"
           " WHERE run_id = %s AND market_ticker = 'T-M2'", (run_id,))
    snapshot_rows = next(rows for name, _ok, rows in portfolio_check(conn, run_id) if name == "snapshots")
    assert snapshot_rows, "tamper did not fail the portfolio snapshots check"
    status, _, body = fetch(ctx["port"], f"/runs/{run_id}")
    assert status == 200, f"status {status}"
    recon = section(body, "Reconciliation")
    assert "FAIL" in recon and "chip-red" in recon, "no FAIL verdict"
    check_rows = table_rows(recon, "check-row")
    assert len(check_rows) == 13
    failing = [r for r in check_rows if r[2] == "Fail"]
    assert failing, check_rows
    assert 'class="examples-row"' in recon, "no examples row under the failing check"
    examples = table_rows(recon, "examples-row")
    assert examples and examples[0][0].strip(), examples
    portfolio = table_rows(section(body, "Portfolio check"))[1:]
    by_name = {r[0]: r[1] for r in portfolio}
    assert by_name["snapshots"] == f"Fail · {len(snapshot_rows)} rows", by_name
    assert by_name["fees"] == "OK", by_name
    assert by_name["settlements"] == "OK", by_name


def test_e11_escaping_everywhere(ctx):
    conn = ctx["conn"]
    cur = conn.cursor()
    ugly_market = UGLY[:60]
    cur.execute("INSERT INTO market (ticker, event_ticker, open_time, close_time)"
                " VALUES (%s, 'EV-0601', now(), now() + interval '1 day')", (ugly_market,))
    conn.commit()
    when = datetime(2026, 6, 1, 10, 0, tzinfo=UTC)
    params = {UGLY: UGLY, "plain": "<b>bold</b>"}
    run_id = insert_run(conn, UGLY, params, entries=[
        (ugly_market, when, [("rejected", 0, None, None, UGLY)]),
        ("T-M1", when, [])])
    status, _, body = fetch(ctx["port"], f"/runs/{run_id}")
    assert status == 200, f"status {status}"
    for raw in ["<img src=x", "<b>bold</b>", "onerror=alert(1)>"]:
        assert raw not in body, f"raw {raw!r} in page"
    assert html.escape(ugly_market) in body, "market not escaped-visible"
    assert body.count(UGLY_ESCAPED) >= 3, f"escaped ugly seen {body.count(UGLY_ESCAPED)} times"
    status, _, body = fetch(ctx["port"], "/runs")
    assert "<img src=x" not in body and UGLY_ESCAPED in body


def test_e12_missing_database_500(ctx):
    server, port = start("kalbt_ui_does_not_exist")
    try:
        status, headers, body = fetch(port, "/runs")
        assert status == 500, f"status {status}"
        assert headers["content-type"].startswith("text/html")
        assert body.startswith("<!doctype html>") and "Server error" in body
        assert "OperationalError" in body, body[-400:]
        status, _, _ = fetch(port, f"/runs/{ctx['clean']}")
        assert status == 500, f"second request {status}"
        for path, expected in [("/static/app.css", 200), ("/nope", 404), ("/", 302)]:
            status, _, _ = fetch(port, path)
            assert status == expected, f"{path} after the 500 gave {status}"
    finally:
        server.shutdown()
        server.server_close()


def test_e13_concurrency(ctx):
    paths = ["/runs", f"/runs/{ctx['clean']}"] * 5
    with ThreadPoolExecutor(max_workers=10) as pool:
        statuses = list(pool.map(lambda p: fetch(ctx["port"], p)[0], paths))
    assert statuses == [200] * 10, statuses


def test_e14_empty_runs_list(ctx):
    assert "No runs yet." in ctx["bare_empty_body"], ctx["bare_empty_body"][-300:]
    assert "<table" not in ctx["bare_empty_body"], "table shown for no runs"


def test_e15_binds_localhost_only(ctx):
    assert ctx["server"].server_address[0] == "127.0.0.1", ctx["server"].server_address


def test_e16_nav_and_title(ctx):
    _, _, body = fetch(ctx["port"], f"/runs/{ctx['clean']}")
    assert f"<title>Run {ctx['clean']} · KalBacktester</title>" in body
    assert 'aria-current="page"' in body
    _, _, nf = fetch(ctx["port"], "/nope")
    assert 'aria-current="page"' not in nf


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
    conns, servers = [], []
    try:
        conn = make_db(admin, DB, True)
        conns.append(conn)
        bare_conn = make_db(admin, BARE_DB, False)
        conns.append(bare_conn)

        server, port = start(DB)
        bare_server, bare_port = start(BARE_DB)
        servers += [server, bare_server]
        bare_empty_body = fetch(bare_port, "/runs")[2]  # before any run exists

        ctx = {"conn": conn, "port": port, "server": server, "bare_port": bare_port,
               "bare_empty_body": bare_empty_body, "live_before": live_before}
        ctx["clean"] = clean_run(conn)
        ctx["bare_clean"] = clean_run(bare_conn)

        run_test("01 GET / redirects to /runs", test_01_root_redirect, ctx)
        run_test("02 /runs lists the clean run with counts", test_02_runs_list, ctx)
        run_test("03 run page: PASS, 13 checks, 3 portfolio OK, journal rows", test_03_run_page, ctx)
        run_test("04 journal: first M1 fill, rejected row, blocked row", test_04_journal_contents, ctx)
        run_test("05 unknown run, non-numeric run and unknown path give 404", test_05_404s, ctx)
        run_test("06 static: css, font, traversal and non-served type", test_06_static, ctx)
        run_test("07 POST /runs gives 405", test_07_post, ctx)
        run_test("08 strategy with <script> is escaped", test_08_escaping, ctx)
        run_test("09 server-style connection is read-only", test_09_read_only, ctx)
        run_test("10 missing portfolio views: page renders with the notice", test_10_bare_database, ctx)
        run_test("11 formatters and chip", test_11_formatters, ctx)
        run_test("12 every page is a full document with no external URL", test_12_full_documents, ctx)
        run_test("E01 static path tricks all 404", test_e01_static_path_tricks, ctx)
        run_test("E01b leading // does not escape or crash", test_e01b_double_slash_prefix, ctx)
        run_test("E02 other vendored files are served with the right type", test_e02_static_ok_files, ctx)
        run_test("E03 odd run ids give 404", test_e03_odd_run_ids, ctx)
        run_test("E04 digit id beyond bigint gives 404, not 500", test_e04_very_long_digit_id_is_404, ctx)
        run_test("E04b 5000-digit id gives 404, not 500", test_e04b_five_thousand_digit_id_is_404, ctx)
        run_test("E04c 17 to 20 digit ids and null bytes give 404", test_e04c_digit_count_boundary, ctx)
        run_test("E05 zero-padded run id does not crash", test_e05_leading_zeros, ctx)
        run_test("E06 query strings do not change routing", test_e06_query_strings, ctx)
        run_test("E07 HEAD, PUT, DELETE, PATCH, OPTIONS give 405 or 501", test_e07_other_methods, ctx)
        run_test("E08 run with no intents renders, counts 0", test_e08_run_without_intents, ctx)
        run_test("E09 intent without outcome shows No outcome", test_e09_intent_without_outcome, ctx)
        run_test("E10 failing portfolio and recon checks show Fail, rows, examples", test_e10_failing_checks, ctx)
        run_test("E11 ticker, reason_code, params key and value are escaped", test_e11_escaping_everywhere, ctx)
        run_test("E12 missing database gives 500 page and server keeps serving", test_e12_missing_database_500, ctx)
        run_test("E13 10 parallel requests all 200", test_e13_concurrency, ctx)
        run_test("E14 no runs: 'No runs yet.' and no table", test_e14_empty_runs_list, ctx)
        run_test("E15 server binds 127.0.0.1", test_e15_binds_localhost_only, ctx)
        run_test("E16 page title and nav state", test_e16_nav_and_title, ctx)
        run_test("13 live kalbt row counts unchanged", test_13_live_untouched, ctx)
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()
        for conn in conns:
            conn.close()
        for name in (DB, BARE_DB):
            admin.execute(f"DROP DATABASE IF EXISTS {name} WITH (FORCE)")
        admin.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
