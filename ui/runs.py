"""Screen 1: the list of runs and the page of one run (parameters, checks, journal)."""
from datetime import UTC

import portfolio.check_run
import recon.check_run
from ui.layout import MISSING, chip, count, esc, money, page, price, table, td

STATUS_CHIPS = {
    "running": ("Running", "warn"),
    "done": ("Done", "ok"),
    "failed": ("Failed", "red"),
    "discarded": ("Discarded", "mute"),
}
OUTCOME_CHIPS = {
    "filled": ("Filled", "ok"),
    "rejected": ("Rejected", "warn"),
    "blocked": ("Blocked", "mute"),
}


def status_chip(status):
    word, kind = STATUS_CHIPS.get(status, (status, "mute"))
    return chip(word, kind)


def utc_text(value, fmt):
    if value is None:
        return MISSING
    return value.astimezone(UTC).strftime(fmt)


# ---------- runs list ----------

RUNS_SQL = """
SELECT r.run_id, r.strategy, r.date_start, r.date_end, r.status, r.starting_cash,
       (SELECT count(*) FROM order_intent i WHERE i.run_id = r.run_id),
       (SELECT count(*) FROM order_outcome o JOIN order_intent i USING (intent_id)
         WHERE i.run_id = r.run_id AND o.outcome = 'filled'),
       (SELECT count(*) FROM order_outcome o JOIN order_intent i USING (intent_id)
         WHERE i.run_id = r.run_id AND o.outcome = 'rejected'),
       (SELECT count(*) FROM order_outcome o JOIN order_intent i USING (intent_id)
         WHERE i.run_id = r.run_id AND o.outcome = 'blocked')
FROM run r
ORDER BY r.run_id DESC
"""


def runs_page(conn):
    cur = conn.cursor()
    cur.execute(RUNS_SQL)
    runs = cur.fetchall()
    if not runs:
        body = '<h1>Runs</h1><p class="panel">No runs yet.</p>'
        return page("Runs", body, "runs")

    head = [("Run", False), ("Strategy", False), ("Dates", False), ("Status", False),
            ("Intents", True), ("Filled", True), ("Rejected", True), ("Blocked", True),
            ("Starting cash", True)]
    rows = []
    for run_id, strategy, start, end, status, cash, intents, filled, rejected, blocked in runs:
        rows.append(
            "<tr>"
            + td(f'<a href="/runs/{run_id}">{run_id}</a>', "mono")
            + td(esc(strategy))
            + td(f"{esc(start)} – {esc(end)}", "mono")
            + td(status_chip(status))
            + td(count(intents), "num")
            + td(count(filled), "num")
            + td(count(rejected), "num")
            + td(count(blocked), "num")
            + td(money(cash), "num")
            + "</tr>"
        )
    body = f'<h1>Runs</h1><div class="panel">{table(head, rows)}</div>'
    return page("Runs", body, "runs")


# ---------- one run ----------

RUN_SQL = """
SELECT run_id, strategy, params, code_version, date_start, date_end, started_at, finished_at, status
FROM run WHERE run_id = %s
"""

JOURNAL_SQL = """
SELECT i.decided_at, i.market_ticker, i.side, i.action, i.quantity,
       o.outcome_id, o.outcome, o.quantity, o.price, o.fee, o.reason_code
FROM order_intent i
LEFT JOIN order_outcome o USING (intent_id)
WHERE i.run_id = %s
ORDER BY i.decided_at, i.intent_id, o.outcome_id
"""


def run_page(conn, run_id):
    cur = conn.cursor()
    cur.execute(RUN_SQL, (run_id,))
    run = cur.fetchone()
    if run is None:
        return None
    sections = [
        heading_section(run),
        params_section(run[2]),
        recon_section(conn, run_id),
        portfolio_section(conn, run_id),
        journal_section(conn, run_id),
    ]
    return page(f"Run {run_id}", "".join(sections), "runs")


def heading_section(run):
    run_id, strategy, _params, code_version, start, end, started_at, finished_at, status = run
    return (
        '<div class="run-heading">'
        f'<h1><span class="mono">Run {run_id}</span> · {esc(strategy)} · '
        f'<span class="mono">{esc(start)} – {esc(end)}</span> {status_chip(status)}</h1>'
        f'<p class="meta"><span class="mono">{esc(code_version)}</span> · started '
        f'<span class="mono">{utc_text(started_at, "%Y-%m-%d %H:%M")}</span> · finished '
        f'<span class="mono">{utc_text(finished_at, "%Y-%m-%d %H:%M")}</span></p>'
        "</div>"
    )


def params_section(params):
    rows = [f'<tr>{td(esc(key), "mono")}{td(esc(params[key]))}</tr>' for key in sorted(params)]
    return f'<section class="panel"><h2>Parameters</h2>{table([("Key", False), ("Value", False)], rows)}</section>'


def recon_section(conn, run_id):
    try:
        report = recon.check_run.check_run(conn, run_id)
    except Exception as error:  # noqa: BLE001 - shown on the page
        conn.rollback()
        return error_section("Reconciliation", error)
    verdict = chip("PASS", "ok") if report["verdict"] == "PASS" else chip("FAIL", "red")
    head = [("Id", True), ("Check", False), ("Status", False), ("Detail", False)]
    rows = []
    for check in report["checks"]:
        status = chip("OK", "ok") if check["status"] == "pass" else chip("Fail", "red")
        rows.append(
            '<tr class="check-row">'
            + td(esc(check["id"]), "num")
            + td(esc(check["name"]))
            + td(status)
            + td(esc(check["detail"]))
            + "</tr>"
        )
        if check["status"] == "fail":
            examples = "<br>".join(esc(example) for example in check["examples"][:10])
            rows.append(f'<tr class="examples-row"><td colspan="4" class="mono">{examples}</td></tr>')
    return f'<section class="panel"><h2>Reconciliation {verdict}</h2>{table(head, rows)}</section>'


def portfolio_section(conn, run_id):
    heading = f'<h2>Portfolio check <a href="/runs/{run_id}/portfolio">Day by day →</a> <a href="/runs/{run_id}/analytics">Analytics →</a></h2>'
    try:
        results = portfolio.check_run.check_run(conn, run_id)
    except RuntimeError:
        conn.rollback()
        return (
            f'<section class="panel">{heading}'
            f'<p>Portfolio views are not applied to this database yet. {chip("Not available", "mute")}</p>'
            "</section>"
        )
    except Exception as error:  # noqa: BLE001 - shown on the page
        conn.rollback()
        return error_section("Portfolio check", error)
    rows = []
    for name, ok, failing_rows in results:
        status = chip("OK", "ok") if ok else chip(f"Fail · {len(failing_rows)} rows", "red")
        rows.append(f"<tr>{td(esc(name), 'mono')}{td(status)}</tr>")
    head = [("Check", False), ("Status", False)]
    return f'<section class="panel">{heading}{table(head, rows)}</section>'


def error_section(title, error):
    message = f"{type(error).__name__}: {error}"
    return f'<section class="panel panel-error"><h2>{esc(title)}</h2><p class="mono">{esc(message)}</p></section>'


def journal_section(conn, run_id):
    cur = conn.cursor()
    cur.execute(JOURNAL_SQL, (run_id,))
    head = [("Decided", False), ("Market", False), ("Side", False), ("Action", False),
            ("Requested", True), ("Outcome", False), ("Qty", True), ("Price", True),
            ("Fee", True), ("Reason", False)]
    rows = []
    for (decided_at, market, side, action, requested,
         outcome_id, outcome, quantity, fill_price, fee, reason_code) in cur.fetchall():
        if outcome_id is None:
            outcome_cell = chip("No outcome", "red")
        else:
            word, kind = OUTCOME_CHIPS[outcome]
            outcome_cell = chip(word, kind)
        reason = esc(reason_code) if reason_code is not None else MISSING
        rows.append(
            '<tr class="journal-row">'
            + td(utc_text(decided_at, "%m-%d %H:%M"), "mono")
            + td(esc(market), "mono")
            + td(esc(side))
            + td(esc(action))
            + td(count(requested), "num")
            + td(outcome_cell)
            + td(count(quantity), "num")
            + td(price(fill_price), "num")
            + td(money(fee), "num")
            + td(reason, "mono")
            + "</tr>"
        )
    return f'<section class="panel"><h2>Journal</h2>{table(head, rows, " id=journal")}</section>'
