"""Screen 5: how complete the data is, per city and week."""
from query import coverage as cov
from ui.layout import MISSING, chip, count, esc, page, table, td

STATUS_KINDS = {"OK": "ok", "Check": "warn", "RED": "red"}
STATUS_ORDER = {"RED": 0, "Check": 1}


def status_kind(status):
    if status not in STATUS_KINDS:
        raise ValueError(f"unknown coverage status {status!r}")
    return STATUS_KINDS[status]


def summary_line(rows):
    weeks = sorted({r["week_start"] for r in rows})
    n_ok = sum(1 for r in rows if r["status"] == "OK")
    n_check = sum(1 for r in rows if r["status"] == "Check")
    n_red = sum(1 for r in rows if r["status"] == "RED")
    text = (f"{len(rows)} city-weeks · {n_ok} OK · {n_check} Check · {n_red} RED"
            f" · weeks {weeks[0]} – {weeks[-1]}")
    return f'<p class="panel">{esc(text)}</p>'


def cell_title(row):
    gate = "–" if row["gate_warnings"] is None else row["gate_warnings"]
    return (f'{row["events"]} events · {row["markets"]} markets · {row["bars"]} bars'
            f' · NWS {row["nws_highs"]} · Kalshi {row["kalshi_highs"]}'
            f' · gate {gate} · numeric {row["numeric_mismatches"]}')


def grid_cell(row):
    if row is None:
        return td(MISSING)
    link = (f'<a href="/markets/{esc(row["underlying_id"])}" title="{esc(cell_title(row))}">'
            f'{chip(row["status"], status_kind(row["status"]))}</a>')
    return td(link)


def grid_section(rows):
    cells = {(r["underlying_id"], r["week_start"]): r for r in rows}
    cities = sorted({r["underlying_id"] for r in rows})
    weeks = sorted({r["week_start"] for r in rows})
    head = [("City", False)] + [(f'<span class="mono">{week}</span>', False) for week in weeks]
    body = []
    for city in cities:
        body.append("<tr>" + td(esc(city), "mono")
                    + "".join(grid_cell(cells.get((city, week))) for week in weeks) + "</tr>")
    return f'<section class="panel"><h2>Grid</h2>{table(head, body, " class=cov-grid")}</section>'


def attention_section(rows):
    problems = [r for r in rows if r["status"] != "OK"]
    if not problems:
        return '<section class="panel"><h2>Needs attention</h2><p>Every loaded city-week is OK.</p></section>'
    problems.sort(key=lambda r: (STATUS_ORDER[r["status"]], r["underlying_id"], r["week_start"]))
    body = []
    for r in problems:
        body.append(
            "<tr>"
            + td(esc(r["underlying_id"]), "mono")
            + td(esc(r["week_start"]), "mono")
            + td(chip(r["status"], status_kind(r["status"])))
            + td(count(r["events"]), "num")
            + td(count(r["markets"]), "num")
            + td(count(r["bars"]), "num")
            + td(count(r["nws_highs"]), "num")
            + td(count(r["kalshi_highs"]), "num")
            + td(count(r["gate_warnings"]), "num")
            + td(count(r["numeric_mismatches"]), "num")
            + "</tr>"
        )
    head = [("City", False), ("Week", False), ("Status", False), ("Events", True), ("Markets", True),
            ("Bars", True), ("NWS highs", True), ("Kalshi highs", True), ("Gate warnings", True),
            ("Numeric mismatches", True)]
    return f'<section class="panel"><h2>Needs attention</h2>{table(head, body)}</section>'


def coverage_page(conn):
    rows = cov.coverage(conn)
    if not rows:
        body = '<h1>Coverage</h1><p class="panel">No coverage data yet.</p>'
    else:
        body = f"<h1>Coverage</h1>{summary_line(rows)}{grid_section(rows)}{attention_section(rows)}"
    return page("Coverage", body, "coverage")
