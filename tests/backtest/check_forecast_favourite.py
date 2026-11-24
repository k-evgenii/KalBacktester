"""Checks the forecast-favourite strategy (spec B016, agreed tests 1 to 9, plus two extra).

Tests 1 to 8 run on a throwaway database kalbt_b016_test (db/schema.sql applied, hand-made
fixture, dropped at the end). Test 9 only reads the live database (SELECT) and compares the
row counts of the live trading tables before and after.

Fixture
  station KTST, offset -5, underlying U1, series S1   (all scenarios but test 1b)
  station KPAC, offset -8, underlying U2, series S2   (test 1b)
  one event per scenario, each on its own date; ladder of six markets per event:
    -LT  less     cap 52          -> whole degrees  <= 51
    -B52 between  52 to 53        -> 52..53
    -B54 between  54 to 55        -> 54..55
    -B56 between  56 to 57        -> 56..57
    -B58 between  58 to 59        -> 58..59
    -GT  greater  floor 59        -> >= 60
  Model forecasts are hourly at lead_day 1 (unless a test says otherwise): 24 hours per
  model, the model's high at local hour 12 and high - 10 at all other hours, so each model's
  daily maximum is exactly the number written in the scenario.
  Decision 09:00 LST at offset -5 is 14:00 UTC. The climate day D at offset -5 is
  D 05:00 UTC to D+1 05:00 UTC; the lead-1 look-ahead check needs
  D+1 04:00 - 24 h = D 04:00 UTC <= 14:00 UTC, which holds.
"""
import subprocess
import sys
import tempfile
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect
from engine.match_intents import read_csv
from strategies.forecast_favourite import DEFAULTS, intents_for_event, intents_for_series

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SCHEMA = REPO_ROOT / "db" / "schema.sql"
TEST_DB = "kalbt_b016_test"
TRADING_TABLES = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]

D = Decimal
UTC = timezone.utc
failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def run_test(name, func, *args):
    try:
        func(*args)
    except Exception:
        report(name, False, traceback.format_exc())
    else:
        report(name, True)


def check(condition, message):
    if not condition:
        raise AssertionError(message)


def utc(day, hour=0):
    return datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(hours=hour)


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def add_event(cur, ticker, series, underlying, event_date):
    """One event with its six-market ladder. Tickers: <event>-LT, -B52, -B54, -B56, -B58, -GT."""
    cur.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                " VALUES (%s, %s, %s, %s, 'NWS')", (ticker, series, underlying, event_date))
    opens = utc(event_date) - timedelta(days=1)
    closes = utc(event_date) + timedelta(days=1)
    ladder = [("LT", "less", None, 52), ("B52", "between", 52, 53), ("B54", "between", 54, 55),
              ("B56", "between", 56, 57), ("B58", "between", 58, 59), ("GT", "greater", 59, None)]
    for suffix, strike_type, floor_strike, cap_strike in ladder:
        cur.execute("INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike,"
                    " open_time, close_time) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                    (f"{ticker}-{suffix}", ticker, strike_type, floor_strike, cap_strike, opens, closes))


def add_model(cur, station, offset, event_date, model, high, lead=1, n_hours=24, extra_hours=()):
    """Hourly forecast for the climate day. Hour 12 holds `high`, the other hours high - 10.
    n_hours=23 leaves out the last hour (hour 23). extra_hours are (hours from day_start, temp)
    rows, which may lie outside the day."""
    day_start = utc(event_date) - timedelta(hours=offset)
    rows = []
    for hour in range(n_hours):
        rows.append((hour, high if hour == 12 else high - 10))
    rows.extend(extra_hours)
    for hour, temp in rows:
        cur.execute("INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f)"
                    " VALUES (%s, %s, %s, %s, %s)",
                    (station, model, day_start + timedelta(hours=hour), lead, D(str(temp))))


def add_models(cur, station, offset, event_date, highs, **kwargs):
    for number, high in enumerate(highs, start=1):
        add_model(cur, station, offset, event_date, f"m{number}", D(str(high)), **kwargs)


def make_scenario(cur, ticker, event_date, highs, station="KTST", offset=-5, series="S1",
                  underlying="U1", **kwargs):
    add_event(cur, ticker, series, underlying, event_date)
    add_models(cur, station, offset, event_date, highs, **kwargs)


# Events by scenario, each on its own date in March 2026
EV_T1A = ("EV-T1A", date(2026, 3, 1))   # offset -5, five models of 56 (mean 56)
EV_T1B = ("EV-T1B", date(2026, 3, 1))   # offset -8 station, five models of 56
EV_T2 = ("EV-T2", date(2026, 3, 2))
EV_T3A = ("EV-T3A", date(2026, 3, 3))
EV_T3B = ("EV-T3B", date(2026, 3, 4))
EV_T4A = ("EV-T4A", date(2026, 3, 5))
EV_T4B = ("EV-T4B", date(2026, 3, 6))
EV_T4C = ("EV-T4C", date(2026, 3, 7))
EV_T4D = ("EV-T4D", date(2026, 3, 8))
EV_T5 = ("EV-T5", date(2026, 3, 20))  # isolated: its outside-day rows would collide with a neighbour day
EV_T6 = ("EV-T6", date(2026, 3, 10))
EV_T7A = ("EV-T7A", date(2026, 3, 11))
EV_T7B = ("EV-T7B", date(2026, 3, 12))
EV_T12 = ("EV-T12", date(2026, 3, 13))  # underlying without a station


def load_fixture(conn):
    cur = conn.cursor()
    for station, offset, iem in (("KTST", -5, "TST"), ("KPAC", -8, "PAC")):
        cur.execute(
            "INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h,"
            " iem_id, iem_network) VALUES (%s, %s, 40, -74, 'America/New_York', %s, %s, 'NY_ASOS')",
            (station, f"Test {station}", offset, iem))
    cur.execute("INSERT INTO underlying (underlying_id, description, station_id) VALUES"
                " ('U1', 'Test city one', 'KTST'), ('U2', 'Test city two', 'KPAC')")
    cur.execute("INSERT INTO series (ticker, underlying_id, title) VALUES"
                " ('S1', 'U1', 'Test series one'), ('S2', 'U2', 'Test series two')")

    # test 1: five models of 56 each at both stations
    make_scenario(cur, *EV_T1A, [56] * 5)
    make_scenario(cur, EV_T1B[0], EV_T1B[1], [56] * 5, station="KPAC", offset=-8,
                  series="S2", underlying="U2")
    # test 2: highs 54, 56, 58, 59, 60 -> sum 287, mean 57.4, rounded 57 -> bracket 56..57
    make_scenario(cur, *EV_T2, [54, 56, 58, 59, 60])
    # test 3a: five complete models of 56 plus model m6 with 23 hours and a high of 80.
    # Ignoring m6: mean 56, five models. Using it by mistake: (280 + 80) / 6 = 60.
    make_scenario(cur, *EV_T3A, [56] * 5)
    add_model(cur.connection.cursor(), "KTST", -5, EV_T3A[1], "m6", D(80), n_hours=23)
    # test 3b: four complete models of 56 plus m5 with 23 hours -> only 4 complete
    make_scenario(cur, *EV_T3B, [56] * 4)
    add_model(cur.connection.cursor(), "KTST", -5, EV_T3B[1], "m5", D(56), n_hours=23)
    # test 4: mean 57.5 -> 58 (bracket 58..59); mean 57.49 -> 57 (bracket 56..57);
    #         mean 59.5 -> 60 (greater);        mean 59.49 -> 59 (bracket 58..59)
    # 55.5 + 57.5 + 58.5 + 57.5 + 58.5 = 287.5 -> 57.5
    make_scenario(cur, *EV_T4A, ["55.5", "57.5", "58.5", "57.5", "58.5"])
    # 55.45 + 57.5 + 58.5 + 57.5 + 58.5 = 287.45 -> 57.49
    make_scenario(cur, *EV_T4B, ["55.45", "57.5", "58.5", "57.5", "58.5"])
    # 57.5 + 59.5 + 60.5 + 59.5 + 60.5 = 297.5 -> 59.5
    make_scenario(cur, *EV_T4C, ["57.5", "59.5", "60.5", "59.5", "60.5"])
    # 57.45 + 59.5 + 60.5 + 59.5 + 60.5 = 297.45 -> 59.49
    make_scenario(cur, *EV_T4D, ["57.45", "59.5", "60.5", "59.5", "60.5"])
    # test 5: five models of 56, and per model two rows outside the climate day at 100 F:
    # hour -1 (one hour before day_start) and hour 24 (= day_end). Used by mistake they would
    # make n_hours 26 (model dropped, too few models) or raise the maximum to 100.
    make_scenario(cur, *EV_T5, [56] * 5, extra_hours=[(-1, 100), (24, 100)])
    # test 6: lead_day 0 data, five models of 56
    make_scenario(cur, *EV_T6, [56] * 5, lead=0)
    # test 7: all models 65 -> greater (>= 60); all models 45 -> less (<= 51)
    make_scenario(cur, *EV_T7A, [65] * 5)
    make_scenario(cur, *EV_T7B, [45] * 5)
    # test 12 (extra): underlying without a station
    cur.execute("INSERT INTO underlying (underlying_id, description, station_id)"
                " VALUES ('U3', 'City without station', NULL)")
    cur.execute("INSERT INTO series (ticker, underlying_id, title) VALUES ('S3', 'U3', 'No station')")
    add_event(cur, EV_T12[0], "S3", "U3", EV_T12[1])
    conn.commit()


def one_intent(intents):
    check(len(intents) == 1, f"expected exactly 1 intent, got {len(intents)}")
    return intents[0]


# ---------------------------------------------------------------------------
# Agreed tests 1 to 8 (database kalbt_b016_test)
# ---------------------------------------------------------------------------

def test_01_decision_time(conn):
    # offset -5: 2026-03-01 09:00 LST = 14:00 UTC; offset -8: 09:00 LST = 17:00 UTC
    intents, info = intents_for_event(conn, EV_T1A[0], dict(DEFAULTS))
    check(info["skipped"] is None, f"-5 station skipped: {info['skipped']}")
    check(one_intent(intents)["decided_at"] == datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
          f"offset -5 gave {intents[0]['decided_at']}")
    check(info["decided_at"] == datetime(2026, 3, 1, 14, 0, tzinfo=UTC),
          f"info decided_at {info['decided_at']}")
    intents, info = intents_for_event(conn, EV_T1B[0], dict(DEFAULTS))
    check(info["skipped"] is None, f"-8 station skipped: {info['skipped']}")
    check(one_intent(intents)["decided_at"] == datetime(2026, 3, 1, 17, 0, tzinfo=UTC),
          f"offset -8 gave {intents[0]['decided_at']}")


def test_02_five_models_buy_yes(conn):
    # highs 54, 56, 58, 59, 60 -> mean 287 / 5 = 57.4 -> rounded 57 -> between 56..57 -> -B56
    params = dict(DEFAULTS, quantity=7)
    intents, info = intents_for_event(conn, EV_T2[0], params)
    intent = one_intent(intents)
    check(intent["market_ticker"] == "EV-T2-B56", f"market {intent['market_ticker']}")
    check(intent["side"] == "yes" and intent["action"] == "buy", f"{intent['side']} {intent['action']}")
    check(intent["quantity"] == 7, f"quantity {intent['quantity']}")
    check(intent["decided_at"] == datetime(2026, 3, 2, 14, 0, tzinfo=UTC), f"{intent['decided_at']}")
    check(intent["reason"] == "forecast 57.4F from 5 models", f"reason {intent['reason']!r}")
    check(info["event_ticker"] == EV_T2[0], f"info event {info['event_ticker']}")
    check(info["n_models"] == 5, f"n_models {info['n_models']}")
    check(D(info["forecast_high"]) == D("57.4"), f"forecast_high {info['forecast_high']}")
    check(info["chosen_market"] == "EV-T2-B56", f"chosen {info['chosen_market']}")
    check(info["skipped"] is None, f"skipped {info['skipped']}")


def test_03_incomplete_model_ignored(conn):
    # 3a: m6 has 23 hours and is ignored -> five models, mean 56, bracket 56..57
    intents, info = intents_for_event(conn, EV_T3A[0], dict(DEFAULTS))
    check(info["skipped"] is None, f"3a skipped: {info['skipped']}")
    check(info["n_models"] == 5, f"3a n_models {info['n_models']}")
    check(D(info["forecast_high"]) == D(56), f"3a forecast_high {info['forecast_high']}")
    check(one_intent(intents)["market_ticker"] == "EV-T3A-B56", f"3a market {intents[0]['market_ticker']}")
    # 3b: only 4 complete models of 5 -> too_few_models, no intent
    intents, info = intents_for_event(conn, EV_T3B[0], dict(DEFAULTS))
    check(info["skipped"] == "too_few_models", f"3b skipped {info['skipped']!r}")
    check(intents == [], f"3b intents {intents}")
    check(info["chosen_market"] is None, f"3b chosen {info['chosen_market']}")


def test_04_rounding(conn):
    cases = [
        (EV_T4A, D("57.5"), "EV-T4A-B58"),    # 57.5 -> 58 (half up)
        (EV_T4B, D("57.49"), "EV-T4B-B56"),   # 57.49 -> 57
        (EV_T4C, D("59.5"), "EV-T4C-GT"),     # 59.5 -> 60 -> greater
        (EV_T4D, D("59.49"), "EV-T4D-B58"),   # 59.49 -> 59
    ]
    for (ticker, _), mean, market in cases:
        intents, info = intents_for_event(conn, ticker, dict(DEFAULTS))
        check(info["skipped"] is None, f"{ticker} skipped {info['skipped']}")
        check(D(info["forecast_high"]) == mean, f"{ticker} mean {info['forecast_high']} expected {mean}")
        check(one_intent(intents)["market_ticker"] == market,
              f"{ticker} market {intents[0]['market_ticker']} expected {market}")


def test_05_rows_outside_climate_day(conn):
    # extra rows at hour -1 and hour 24 must not count: five models, mean 56, bracket 56..57
    intents, info = intents_for_event(conn, EV_T5[0], dict(DEFAULTS))
    check(info["skipped"] is None, f"skipped {info['skipped']}")
    check(info["n_models"] == 5, f"n_models {info['n_models']}")
    check(D(info["forecast_high"]) == D(56), f"forecast_high {info['forecast_high']}")
    check(one_intent(intents)["market_ticker"] == "EV-T5-B56", f"market {intents[0]['market_ticker']}")


def test_06_lead_zero_lookahead(conn):
    # lead 0: data valid up to D+1 04:00 UTC would be "known" at 14:00 UTC on D -> lookahead
    intents, info = intents_for_event(conn, EV_T6[0], dict(DEFAULTS, lead_day=0))
    check(info["skipped"] == "lookahead", f"skipped {info['skipped']!r}")
    check(intents == [], f"intents {intents}")
    check(info["chosen_market"] is None, f"chosen {info['chosen_market']}")


def test_07_tail_markets(conn):
    intents, info = intents_for_event(conn, EV_T7A[0], dict(DEFAULTS))
    check(one_intent(intents)["market_ticker"] == "EV-T7A-GT",
          f"65 F gave {intents[0]['market_ticker']}")
    intents, info = intents_for_event(conn, EV_T7B[0], dict(DEFAULTS))
    check(one_intent(intents)["market_ticker"] == "EV-T7B-LT",
          f"45 F gave {intents[0]['market_ticker']}")


def test_08_cli_csv(conn):
    start, end = date(2026, 3, 1), date(2026, 3, 20)
    params = dict(DEFAULTS, quantity=3)
    intents, infos = intents_for_series(conn, "S1", start, end, params)
    check(len(intents) >= 10, f"fixture should give many intents, got {len(intents)}")
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "intents.csv"
        result = subprocess.run(
            [sys.executable, "-m", "strategies.forecast_favourite", "--series", "S1",
             "--start", start.isoformat(), "--end", end.isoformat(), "--out", str(out),
             "--db", TEST_DB, "--quantity", "3"],
            cwd=REPO_ROOT, capture_output=True, text=True)
        check(result.returncode == 0, f"CLI exit {result.returncode}: {result.stderr[-600:]}")
        check(out.exists(), "CLI wrote no file")
        loaded = read_csv(out)
    check(loaded == intents, f"CSV intents differ:\nCLI: {loaded}\nfunction: {intents}")
    check(all(intent["quantity"] == 3 for intent in loaded), "quantity not 3")


# ---------------------------------------------------------------------------
# Extra tests
# ---------------------------------------------------------------------------

def test_10_series_order_and_range(conn):
    """(extra) series call: infos in date order, only dates inside the range, one per event."""
    intents, infos = intents_for_series(conn, "S1", date(2026, 3, 2), date(2026, 3, 4), dict(DEFAULTS))
    tickers = [info["event_ticker"] for info in infos]
    check(tickers == [EV_T2[0], EV_T3A[0], EV_T3B[0]], f"infos {tickers}")
    check([i["market_ticker"] for i in intents] == ["EV-T2-B56", "EV-T3A-B56"],
          f"intents {[i['market_ticker'] for i in intents]}")


def test_11_min_models_exact(conn):
    """(extra) five complete models is enough, min_models=6 turns the same event into a skip."""
    intents, info = intents_for_event(conn, EV_T2[0], dict(DEFAULTS, min_models=6))
    check(info["skipped"] == "too_few_models", f"skipped {info['skipped']!r}")
    check(intents == [], f"intents {intents}")


def test_12_no_station(conn):
    """(extra) underlying with station_id NULL: skipped no_station, no exception, no intent."""
    intents, info = intents_for_event(conn, EV_T12[0], dict(DEFAULTS))
    check(info["skipped"] == "no_station", f"skipped {info['skipped']!r}")
    check(intents == [], f"intents {intents}")
    check(info["chosen_market"] is None, f"chosen {info['chosen_market']}")


# ---------------------------------------------------------------------------
# Agreed test 9 (live database, SELECT only)
# ---------------------------------------------------------------------------

def row_counts(conn):
    counts = {}
    for table in TRADING_TABLES:
        counts[table] = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
    return counts


def test_09_live_week(live_conn, live_before):
    intents, infos = intents_for_series(live_conn, "KXHIGHNY", date(2026, 11, 16), date(2026, 11, 22),
                                        dict(DEFAULTS))
    check(len(infos) == 7, f"expected 7 infos, got {len(infos)}")
    event_of = dict(live_conn.execute("SELECT ticker, event_ticker FROM market").fetchall())
    chosen_events = {info["chosen_market"]: info["event_ticker"] for info in infos if info["chosen_market"]}
    for intent in intents:
        market = intent["market_ticker"]
        check(market in event_of, f"unknown market {market}")
        check(chosen_events.get(market) == event_of[market],
              f"market {market} of event {event_of[market]} is not the chosen market of its info")
    nov18 = [info for info in infos if info["event_ticker"] == "KXHIGHNY-26NOV18"]
    check(len(nov18) == 1, "no info for KXHIGHNY-26NOV18")
    check(nov18[0]["chosen_market"] == "KXHIGHNY-26NOV18-B60.5", f"chosen {nov18[0]['chosen_market']}")
    nov18_intents = [i for i in intents if i["market_ticker"] == "KXHIGHNY-26NOV18-B60.5"]
    check(len(nov18_intents) == 1, f"{len(nov18_intents)} intents on B60.5")
    check(nov18_intents[0]["decided_at"] == datetime(2026, 11, 18, 14, 0, tzinfo=UTC),
          f"decided_at {nov18_intents[0]['decided_at']}")
    live_after = row_counts(live_conn)
    check(live_after == live_before, f"live trading tables changed: {live_before} -> {live_after}")


def main():
    live_conn = connect()
    live_before = row_counts(live_conn)  # SELECT count(*) only
    admin = connect()
    admin.autocommit = True
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    conn = None
    try:
        conn = connect(dbname=TEST_DB)
        conn.execute(SCHEMA.read_text())
        conn.commit()
        load_fixture(conn)

        run_test("01 decision time 09:00 LST is 14:00 UTC at -5 and 17:00 UTC at -8", test_01_decision_time, conn)
        run_test("02 five models: mean, rounded value, chosen market, one buy-yes intent", test_02_five_models_buy_yes, conn)
        run_test("03 23-hour model ignored; 4 complete models skip too_few_models", test_03_incomplete_model_ignored, conn)
        run_test("04 rounding half up and bracket choice", test_04_rounding, conn)
        run_test("05 rows outside the climate day are not used", test_05_rows_outside_climate_day, conn)
        run_test("06 lead_day 0 with a 09:00 decision skips lookahead", test_06_lead_zero_lookahead, conn)
        run_test("07 above the top floor gives greater, below the cap gives less", test_07_tail_markets, conn)
        run_test("08 CLI CSV loads with read_csv into the same intents", test_08_cli_csv, conn)
        run_test("10 (extra) series call: date order and range", test_10_series_order_and_range, conn)
        run_test("11 (extra) min_models parameter is honoured", test_11_min_models_exact, conn)
        run_test("12 (extra) underlying without a station skips no_station", test_12_no_station, conn)
        run_test("09 live database: November week, read-only", test_09_live_week, live_conn, live_before)
    finally:
        if conn is not None:
            conn.close()
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.close()
        live_conn.close()

    print(f"\n{len(failures)} failed" if failures else "\nall passed")
    raise SystemExit(1 if failures else 0)


if __name__ == "__main__":
    main()
