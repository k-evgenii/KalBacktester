# B022 — One run of a strategy across every loaded city (the portfolio run)

Track: B backtest. Status: APPROVED (user, 2026-11-24: "positions and exposure across cities over time",
"run at least one demo strategy end to end and see the result in the visualiser"; `run_all` makes one run
per city, so a cross-city portfolio needs one run holding all cities; user away, lead proceeds).

## Goal
`backtest/run_portfolio.py` collects one strategy's intents for every loaded series into a single
engine run (one starting cash, all cities), reconciles it, and keeps it only with `--save`.

## Not built
- No change to `run_all`, the engine or the strategies; no per-city cash split; no JSON report file.

## Files
- `backtest/run_portfolio.py` (new)

## Interfaces
```python
def run_portfolio(conn, strategy, series_tickers, save) -> dict
```
`series_tickers`: list of tickers to include, or None for every series from `loaded_series(conn)`.
Returns `{"strategy", "series": [{"series_ticker", "n_events", "n_intents", "skipped_days"}],
"n_intents", "status" ("ran" | "no_intents"), "run_id", "recon_verdict", "failed_checks", "outcomes"
({"filled","rejected","blocked"}), "settlements", "saved"}`.
CLI: `uv run python -m backtest.run_portfolio --db NAME --strategy {forecast_favourite,midday_fade}
[--series T ...] [--save]` (`--db` required, no default, as run_all). Prints one line per series
(`ticker  events N  intents N  skipped {...}`), then one result line
(`run R  intents N  recon PASS  filled/rejected/blocked a/b/c  settlements S  saved|not saved`), and when
saved `open http://127.0.0.1:8765/runs/R/analytics`. Exit 0 when status is no_intents or recon PASS,
else 1.

## Hard parts (use as written)
```python
from backtest.run_all import STRATEGIES, loaded_series


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
```

## Agreed tests (`tests/backtest/check_run_portfolio.py`, qa-tester)
All on the live database, never saved (no `--save`, `save=False`); live row counts of run,
order_intent, order_outcome, settlement are compared before and after every test and must be unchanged.
1. `run_portfolio(conn, "midday_fade", None, False)`: status "ran", recon_verdict "PASS", one entry per
   `loaded_series(conn)` row (6 today) in the same order, `n_intents` = sum of the per-series
   n_intents, `filled + blocked <= n_intents <= filled + rejected + blocked` (an intent has one outcome
   row, or a filled plus a rejected row when partly filled; recon check enforces that per intent),
   saved False.
2. Same with `["KXHIGHNY"]`: exactly one series entry; n_intents 19 and settlements 19 (B021's
   measured NY result; if the data changed, the qa-tester reports it rather than changing the number).
3. `run_portfolio(conn, "forecast_favourite", None, False)`: status "ran", recon "PASS",
   more than one series entry with n_intents > 0.
4. A series ticker with no loaded data (`["KXHIGHZZZ"]`) → status "no_intents", run_id None.
5. CLI `--db kalbt --strategy midday_fade --series KXHIGHNY` exits 0 and prints `recon PASS` and
   `not saved`; CLI with `--strategy nope` exits 2 (argparse); CLI without `--db` exits 2.

## Test files
- `tests/backtest/check_run_portfolio.py`

## Legacy reference
none

## Done when
- `PYTHONPATH=. uv run python tests/backtest/check_run_portfolio.py` exits 0
- lead runs `uvx ruff check backtest/run_portfolio.py tests/backtest/check_run_portfolio.py`

If anything here is unclear or looks wrong: stop and report, do not guess.
