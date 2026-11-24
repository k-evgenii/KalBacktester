"""Checks the portfolio run (spec B022, agreed tests 1 to 5).

Everything runs against the live database kalbt and is never saved. The row counts of run,
order_intent, order_outcome and settlement are compared before and after every test.
"""
import subprocess
import sys
import traceback
from pathlib import Path

from backtest.run_all import loaded_series
from backtest.run_portfolio import run_portfolio
from db.connect import connect

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
TABLES = ["run", "order_intent", "order_outcome", "settlement"]
failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def row_counts():
    conn = connect()
    try:
        return {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in TABLES}
    finally:
        conn.close()


def run_test(name, func):
    """Run one test with its own connection; live row counts must not change."""
    before = row_counts()
    conn = connect()
    try:
        func(conn)
        after = row_counts()
        check(after == before, f"live tables changed: {before} -> {after}")
    except Exception:  # noqa: BLE001
        report(name, False, traceback.format_exc())
    else:
        report(name, True)
    finally:
        conn.close()


def cli(*args):
    return subprocess.run([sys.executable, "-m", "backtest.run_portfolio", *args],
                          check=False, cwd=REPO_ROOT, capture_output=True, text=True)


def test_01_all_series_midday_fade(conn):
    expected = loaded_series(conn)
    result = run_portfolio(conn, "midday_fade", None, False)
    check(result["status"] == "ran", f"status {result['status']}")
    check(result["recon_verdict"] == "PASS", f"recon {result['recon_verdict']} {result['failed_checks']}")
    check([s["series_ticker"] for s in result["series"]] == [s["series_ticker"] for s in expected],
          f"series {[s['series_ticker'] for s in result['series']]}")
    check(len(expected) == 6, f"loaded_series has {len(expected)} rows, spec says 6 today")
    check(result["n_intents"] == sum(s["n_intents"] for s in result["series"]), "n_intents != sum")
    o = result["outcomes"]
    low = o["filled"] + o["blocked"]
    high = o["filled"] + o["rejected"] + o["blocked"]
    check(low <= result["n_intents"] <= high, f"outcomes {o}, n_intents {result['n_intents']}")
    check(result["saved"] is False, "saved is not False")


def test_02_new_york_only(conn):
    result = run_portfolio(conn, "midday_fade", ["KXHIGHNY"], False)
    check(len(result["series"]) == 1, f"{len(result['series'])} series entries")
    check(result["series"][0]["series_ticker"] == "KXHIGHNY", f"{result['series'][0]['series_ticker']}")
    check(result["n_intents"] == 19, f"n_intents {result['n_intents']} (spec 19)")
    check(result["settlements"] == 19, f"settlements {result['settlements']} (spec 19)")


def test_03_forecast_favourite_all(conn):
    result = run_portfolio(conn, "forecast_favourite", None, False)
    check(result["status"] == "ran", f"status {result['status']}")
    check(result["recon_verdict"] == "PASS", f"recon {result['recon_verdict']} {result['failed_checks']}")
    check(len(result["series"]) > 1, f"{len(result['series'])} series entries")
    check(all(s["n_intents"] > 0 for s in result["series"]),
          f"per series {[(s['series_ticker'], s['n_intents']) for s in result['series']]}")


def test_04_unknown_series(conn):
    result = run_portfolio(conn, "midday_fade", ["KXHIGHZZZ"], False)
    check(result["status"] == "no_intents", f"status {result['status']}")
    check(result["run_id"] is None, f"run_id {result['run_id']}")


def test_05_cli(conn):
    ok = cli("--db", "kalbt", "--strategy", "midday_fade", "--series", "KXHIGHNY")
    check(ok.returncode == 0, f"exit {ok.returncode}: {ok.stderr[-600:]}")
    check("recon PASS" in ok.stdout, f"stdout {ok.stdout!r}")
    check("not saved" in ok.stdout, f"stdout {ok.stdout!r}")
    bad_strategy = cli("--db", "kalbt", "--strategy", "nope")
    check(bad_strategy.returncode == 2, f"--strategy nope exit {bad_strategy.returncode}")
    no_db = cli("--strategy", "midday_fade")
    check(no_db.returncode == 2, f"no --db exit {no_db.returncode}")


def main():
    run_test("01 midday_fade over every loaded series: one run, PASS, counts add up", test_01_all_series_midday_fade)
    run_test("02 midday_fade on KXHIGHNY only: 19 intents, 19 settlements", test_02_new_york_only)
    run_test("03 forecast_favourite over every series: PASS, several series with intents", test_03_forecast_favourite_all)
    run_test("04 series with no loaded data gives no_intents and no run_id", test_04_unknown_series)
    run_test("05 CLI: success output, argparse exits 2", test_05_cli)
    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
