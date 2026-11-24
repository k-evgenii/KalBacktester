"""Run the forecast_favourite strategy over every loaded city series, reconcile each run, summarise.

Run from the repo root:
    uv run python -m backtest.run_all --db NAME [--dry] [--series TICKER ...]
Runs are permanent: a series whose identical run is already done is skipped (not repeated).
--dry writes nothing. Rules are in .claude/specs/B017-backtest-harness.md.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

from psycopg.types.json import Jsonb

from db.connect import connect
from engine.match_intents import match_intents
from recon.check_run import check_run
from strategies.forecast_favourite import DEFAULTS, intents_for_series

REPORT_DIR = Path(__file__).resolve().parent / "reports"

LOADED_SERIES_SQL = """
SELECT e.series_ticker, e.underlying_id, u.station_id,
       min(e.event_date), max(e.event_date), count(*)
FROM event e
JOIN underlying u ON u.underlying_id = e.underlying_id
WHERE EXISTS (SELECT 1 FROM market m JOIN candle c ON c.market_ticker = m.ticker
              WHERE m.event_ticker = e.ticker)
  AND EXISTS (SELECT 1 FROM official_daily o
              WHERE o.station_id = u.station_id AND o.climate_date = e.event_date
                AND o.source = 'kalshi')
GROUP BY e.series_ticker, e.underlying_id, u.station_id
ORDER BY e.series_ticker
"""

EXISTING_RUN_SQL = """
SELECT run_id FROM run
WHERE strategy = 'forecast_favourite' AND status = 'done'
  AND date_start = %(start)s AND date_end = %(end)s
  AND params @> %(strategy_params)s::jsonb
  AND EXISTS (SELECT 1 FROM order_intent i JOIN market m ON m.ticker = i.market_ticker
              JOIN event e ON e.ticker = m.event_ticker
              WHERE i.run_id = run.run_id AND e.series_ticker = %(series)s)
ORDER BY run_id LIMIT 1
"""


def loaded_series(conn):
    """Series with at least one event that has candles and a kalshi official value."""
    with conn.cursor() as cur:
        cur.execute(LOADED_SERIES_SQL)
        rows = cur.fetchall()
    return [
        {
            "series_ticker": r[0], "underlying_id": r[1], "station_id": r[2],
            "date_start": r[3], "date_end": r[4], "n_events": r[5],
        }
        for r in rows
    ]


def find_existing_run(conn, series_ticker, intents, params):
    """run_id of an identical done run of this series, or None."""
    decided = [i["decided_at"] for i in intents]
    with conn.cursor() as cur:
        cur.execute(
            EXISTING_RUN_SQL,
            {
                "start": min(decided).astimezone(UTC).date(),
                "end": max(decided).astimezone(UTC).date(),
                "strategy_params": Jsonb(params),
                "series": series_ticker,
            },
        )
        row = cur.fetchone()
    return row[0] if row else None


def run_series(conn, series, params, dry):
    series_ticker = series["series_ticker"]
    intents, infos = intents_for_series(
        conn, series_ticker, series["date_start"], series["date_end"], params
    )
    skipped_days = dict(Counter(info["skipped"] for info in infos if info["skipped"]))

    result = {
        "series_ticker": series_ticker,
        "date_start": series["date_start"].isoformat(),
        "date_end": series["date_end"].isoformat(),
        "n_events": series["n_events"],
        "n_intents": len(intents),
        "skipped_days": skipped_days,
        "status": None,
        "run_id": None,
        "recon_verdict": None,
        "failed_checks": [],
        "outcomes": None,
        "settlements": None,
    }

    if not intents:
        result["status"] = "no_intents"
        return result

    existing = find_existing_run(conn, series_ticker, intents, params)
    if existing is not None:
        result["status"] = "already_done"
        result["run_id"] = existing
        return result

    if dry:
        result["status"] = "dry"
        return result

    run_id = match_intents(conn, "forecast_favourite", params, intents)
    report = check_run(conn, run_id)
    result["status"] = "ran"
    result["run_id"] = run_id
    result["recon_verdict"] = report["verdict"]
    result["failed_checks"] = [c["id"] for c in report["checks"] if c["status"] == "fail"]
    result["outcomes"] = {k: report["counts"][k] for k in ("filled", "rejected", "blocked")}
    result["settlements"] = report["counts"]["settlements"]
    return result


def print_result(r):
    line = (
        f"{r['series_ticker']}  {r['date_start']}..{r['date_end']}  events {r['n_events']}  "
        f"intents {r['n_intents']}  skipped {r['skipped_days']}  {r['status']}"
    )
    if r["run_id"] is not None:
        line += f"  run {r['run_id']}"
    if r["recon_verdict"] is not None:
        line += f"  recon {r['recon_verdict']}"
        if r["failed_checks"]:
            line += f" (failed {r['failed_checks']})"
        o = r["outcomes"]
        line += (
            f"  filled/rejected/blocked {o['filled']}/{o['rejected']}/{o['blocked']}"
            f"  settlements {r['settlements']}"
        )
    print(line)


def main():
    parser = argparse.ArgumentParser(description="Run the reference strategy over every loaded series.")
    parser.add_argument("--db", required=True, help="database name (no default, runs are permanent)")
    parser.add_argument("--dry", action="store_true", help="list what would run, write nothing")
    parser.add_argument("--series", nargs="+", help="only these series tickers")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        all_series = loaded_series(conn)
        if args.series:
            all_series = [s for s in all_series if s["series_ticker"] in args.series]
        results = []
        for series in all_series:
            result = run_series(conn, series, dict(DEFAULTS), args.dry)
            print_result(result)
            results.append(result)
    finally:
        conn.close()

    counts = Counter(r["status"] for r in results)
    print(f"total: {len(results)} series  {dict(counts)}")

    REPORT_DIR.mkdir(exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = REPORT_DIR / f"run_all_{stamp}.json"
    path.write_text(json.dumps(results, indent=2))
    print(f"report written to {path}")

    all_pass = all(r["recon_verdict"] == "PASS" for r in results if r["status"] == "ran")
    sys.exit(0 if all_pass else 1)


if __name__ == "__main__":
    main()
