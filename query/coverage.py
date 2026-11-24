"""Read-only coverage per city and week: how much data is loaded and whether it can be trusted."""

import json
from datetime import date, timedelta
from pathlib import Path

from psycopg.rows import dict_row

REPO_ROOT = Path(__file__).resolve().parent.parent

COVERAGE_SQL = """
WITH city AS (
  SELECT u.underlying_id, s.ticker AS series_ticker, u.station_id
  FROM underlying u JOIN series s USING (underlying_id) WHERE u.station_id IS NOT NULL),
weeks AS (
  SELECT c.underlying_id, w::date AS week_start
  FROM city c, generate_series(%(start)s::date, %(end)s::date, interval '7 days') w
  UNION
  SELECT e.underlying_id, date_trunc('week', e.event_date)::date FROM event e),
ev AS (
  SELECT e.underlying_id, date_trunc('week', e.event_date)::date AS week_start,
         count(DISTINCT e.ticker) AS events, count(DISTINCT m.ticker) AS markets
  FROM event e LEFT JOIN market m ON m.event_ticker = e.ticker GROUP BY 1, 2),
bars AS (
  SELECT e.underlying_id, date_trunc('week', e.event_date)::date AS week_start, count(*) AS bars
  FROM candle c JOIN market m ON m.ticker = c.market_ticker JOIN event e ON e.ticker = m.event_ticker
  GROUP BY 1, 2),
hi AS (
  SELECT c.underlying_id, date_trunc('week', d.climate_date)::date AS week_start,
         count(*) FILTER (WHERE d.source = 'nws_cli') AS nws_highs,
         count(*) FILTER (WHERE d.source = 'kalshi') AS kalshi_highs
  FROM official_daily d JOIN city c ON c.station_id = d.station_id GROUP BY 1, 2),
mm AS (
  SELECT c.underlying_id, date_trunc('week', k.climate_date)::date AS week_start, count(*) AS numeric_mismatches
  FROM official_daily k JOIN official_daily n
    ON n.station_id = k.station_id AND n.climate_date = k.climate_date AND n.source = 'nws_cli'
  JOIN city c ON c.station_id = k.station_id
  WHERE k.source = 'kalshi' AND k.high_f <> n.high_f GROUP BY 1, 2)
SELECT c.underlying_id, c.series_ticker, c.station_id, w.week_start,
       coalesce(ev.events, 0) AS events, coalesce(ev.markets, 0) AS markets, coalesce(bars.bars, 0) AS bars,
       coalesce(hi.nws_highs, 0) AS nws_highs, coalesce(hi.kalshi_highs, 0) AS kalshi_highs,
       coalesce(mm.numeric_mismatches, 0) AS numeric_mismatches
FROM city c JOIN weeks w USING (underlying_id)
LEFT JOIN ev   USING (underlying_id, week_start)
LEFT JOIN bars USING (underlying_id, week_start)
LEFT JOIN hi   USING (underlying_id, week_start)
LEFT JOIN mm   USING (underlying_id, week_start)
ORDER BY c.underlying_id, w.week_start;
"""


def status(events, nws_highs, kalshi_highs, numeric_mismatches):
    if events < 7 or numeric_mismatches >= 2:
        return "RED"
    if nws_highs < events or kalshi_highs < events or numeric_mismatches == 1:
        return "Check"
    return "OK"


def gate_warnings(series_ticker, week_start):
    # Number of failed WARN checks in the dump's QA report; None if the report is missing or unreadable.
    week_end = week_start + timedelta(days=6)
    folder = f"{series_ticker}_{week_start}_{week_end}"
    path = REPO_ROOT / "api_probe" / "kalshi" / folder / "_qa_report.json"
    try:
        report = json.loads(path.read_text())
        return sum(1 for c in report["checks"] if c["severity"] == "WARN" and c["status"] == "fail")
    except (OSError, ValueError, KeyError, TypeError):
        return None


def coverage(conn, start=date(2026, 8, 24), end=date(2026, 11, 22)):
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(COVERAGE_SQL, {"start": start, "end": end})
        rows = cur.fetchall()
    for row in rows:
        row["gate_warnings"] = gate_warnings(row["series_ticker"], row["week_start"])
        row["status"] = status(row["events"], row["nws_highs"], row["kalshi_highs"], row["numeric_mismatches"])
    return rows
