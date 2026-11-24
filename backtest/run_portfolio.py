"""Run one strategy across every loaded city series as a single engine run, reconcile it.

Run from the repo root:
    uv run python -m backtest.run_portfolio --db NAME --strategy NAME [--series TICKER ...] [--save]
The run is reconciled, then rolled back unless --save is given.
Rules are in .claude/specs/B022-portfolio-run.md.
"""
import argparse
import sys
from collections import Counter

from backtest.run_all import STRATEGIES, loaded_series
from db.connect import connect
from engine.match_intents import match_intents
from recon.check_run import check_run


def run_portfolio(conn, strategy, series_tickers, save):
    module = STRATEGIES[strategy]
    params = dict(module.DEFAULTS)
    all_series = loaded_series(conn)
    if series_tickers:
        all_series = [s for s in all_series if s["series_ticker"] in series_tickers]
    result = {"strategy": strategy, "series": [], "n_intents": 0, "status": None, "run_id": None,
              "recon_verdict": None, "failed_checks": [], "outcomes": None, "settlements": None,
              "saved": False}
    intents = []
    for s in all_series:
        found, infos = module.intents_for_series(conn, s["series_ticker"], s["date_start"], s["date_end"], params)
        intents.extend(found)
        result["series"].append({
            "series_ticker": s["series_ticker"], "n_events": s["n_events"], "n_intents": len(found),
            "skipped_days": dict(Counter(i["skipped"] for i in infos if i["skipped"])),
        })
    result["n_intents"] = len(intents)
    if not intents:
        result["status"] = "no_intents"
        return result
    intents.sort(key=lambda i: (i["decided_at"], i["market_ticker"]))
    try:
        run_id = match_intents(conn, strategy, params, intents)
        report = check_run(conn, run_id)      # same connection: sees the uncommitted rows
    except Exception:
        conn.rollback()
        raise
    if save:
        conn.commit()
    else:
        conn.rollback()
    result.update({
        "status": "ran", "run_id": run_id, "saved": save, "recon_verdict": report["verdict"],
        "failed_checks": [c["id"] for c in report["checks"] if c["status"] == "fail"],
        "outcomes": {k: report["counts"][k] for k in ("filled", "rejected", "blocked")},
        "settlements": report["counts"]["settlements"],
    })
    return result


def print_result(result):
    for s in result["series"]:
        print(f"{s['series_ticker']}  events {s['n_events']}  intents {s['n_intents']}  "
              f"skipped {s['skipped_days']}")
    if result["status"] == "no_intents":
        print("no intents")
        return
    o = result["outcomes"]
    line = f"run {result['run_id']}  intents {result['n_intents']}  recon {result['recon_verdict']}"
    if result["failed_checks"]:
        line += f" (failed {result['failed_checks']})"
    line += (
        f"  filled/rejected/blocked {o['filled']}/{o['rejected']}/{o['blocked']}"
        f"  settlements {result['settlements']}"
    )
    line += "  saved" if result["saved"] else "  not saved"
    print(line)
    if result["saved"]:
        print(f"open http://127.0.0.1:8765/runs/{result['run_id']}/analytics")


def main():
    parser = argparse.ArgumentParser(description="Run one strategy across every loaded series as one run.")
    parser.add_argument("--db", required=True, help="database name (no default)")
    parser.add_argument("--strategy", required=True, choices=sorted(STRATEGIES))
    parser.add_argument("--series", nargs="+", help="only these series tickers")
    parser.add_argument("--save", action="store_true", help="keep the run (default: roll back)")
    args = parser.parse_args()

    conn = connect(args.db)
    try:
        result = run_portfolio(conn, args.strategy, args.series, args.save)
    finally:
        conn.close()

    print_result(result)
    ok = result["status"] == "no_intents" or result["recon_verdict"] == "PASS"
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
