"""Checks the matching engine (spec 012, agreed tests 1 to 18, plus one extra).

The journal tables are append-only, so nothing here writes to the live database. The script
creates a throwaway database kalbt_engine_test, applies db/schema.sql, inserts a small
hand-made fixture, runs one match_intents call per scenario, and drops the database at the end.
Every expected value is worked out by hand in a comment next to it.

Fixture: station KTST (offset -5), event EV-0601 on 2026-06-01 with markets
  M1 (settled yes), M2 (settled no), M3 (unsettled), M5 (unsettled), M6 (settled no),
and event EV-0602 (no nws_cli row) with market M4.
All markets open 2026-05-31 15:00 UTC and close 2026-06-02 06:00 UTC; settled markets settle
2026-06-02 12:00 UTC. Official high time "300 PM" at offset -5 gives lock 2026-06-01 20:00 UTC.
"""
import traceback
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect
from engine.match_intents import match_intents

SCHEMA = Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql"
TEST_DB = "kalbt_engine_test"
TABLES = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]

D = Decimal
M1, M2, M3, M4, M5, M6 = "T-M1", "T-M2", "T-M3", "T-M4", "T-M5", "T-M6"
M7, M8, M9, M10 = "T-M7", "T-M8", "T-M9", "T-M10"
SETTLED_AT = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def at(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def intent(decided_at, ticker, side, action, quantity, reason=None):
    return {"decided_at": decided_at, "market_ticker": ticker, "side": side,
            "action": action, "quantity": quantity, "reason": reason}


def filled(quantity, price, fee, occurred_at):
    return ("filled", None, D(quantity), D(price), D(fee), occurred_at)


def refused(outcome, reason_code, occurred_at):
    """A rejected or blocked row: quantity 0, no price, no fee."""
    return (outcome, reason_code, D(0), None, None, occurred_at)


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def candles():
    """(market, ts, yes_bid, yes_ask, volume). Each scenario owns its own time slot."""
    rows = []

    def add(market, ts, bid, ask, volume):
        rows.append((market, ts, None if bid is None else D(bid),
                     None if ask is None else D(ask), D(volume)))

    # test 1: 05-31 16:00, buy yes
    add(M1, at(5, 31, 16, 0), "0.40", "0.45", "200")
    add(M1, at(5, 31, 16, 1), "0.40", "0.45", "0")
    # test 11: 05-31 18:00, quote changes between T and T+1
    add(M1, at(5, 31, 18, 0), "0.30", "0.35", "100")
    add(M1, at(5, 31, 18, 1), "0.32", "0.38", "50")
    # test 12: 05-31 20:00, no candle at 20:01; the 20:02 candle must not be used
    add(M1, at(5, 31, 19, 58), "0.50", "0.55", "100")
    add(M1, at(5, 31, 20, 0), "0.52", "0.57", "100")
    add(M1, at(5, 31, 20, 2), "0.60", "0.65", "100")
    # test 5: 05-31 22:00, NULL ask; M5 has no candle before 06-01 12:00
    add(M1, at(5, 31, 22, 0), "0.20", None, "100")
    add(M1, at(5, 31, 22, 1), "0.20", None, "0")
    add(M5, at(6, 1, 12, 0), "0.50", "0.55", "100")
    # test 7: 06-01 02:00, window volume 60.4 + 66.0 = 126.4
    add(M1, at(6, 1, 1, 30), "0.45", "0.47", "60.4")
    add(M1, at(6, 1, 2, 0), "0.45", "0.47", "66.0")
    add(M1, at(6, 1, 2, 1), "0.45", "0.47", "0")
    # test 15: M2 06-01 03:00
    add(M2, at(6, 1, 3, 0), "0.45", "0.47", "200")
    add(M2, at(6, 1, 3, 1), "0.45", "0.47", "0")
    # test 8: 06-01 05:00, big volume at 04:00 lies outside the window (04:00, 05:00]
    add(M1, at(6, 1, 4, 0), "0.50", "0.55", "500")
    add(M1, at(6, 1, 5, 0), "0.50", "0.55", "0")
    add(M1, at(6, 1, 5, 1), "0.50", "0.55", "0")
    # test 19: M6 06-01 06:00
    for hour_minute, bid, ask, volume in [((6, 0), "0.40", "0.45", "100"), ((6, 1), "0.40", "0.45", "0"),
                                          ((6, 10), "0.40", "0.45", "100"), ((6, 11), "0.40", "0.45", "0"),
                                          ((6, 20), "0.60", "0.65", "100"), ((6, 21), "0.60", "0.65", "0")]:
        add(M6, at(6, 1, *hour_minute), bid, ask, volume)
    # test 2: 06-01 08:00, buy no
    add(M1, at(6, 1, 8, 0), "0.60", "0.64", "100")
    add(M1, at(6, 1, 8, 1), "0.62", "0.66", "0")
    # test 14: M2 05-31 17:00 and 06-01 09:00
    add(M2, at(5, 31, 17, 0), "0.30", "0.35", "100")
    add(M2, at(5, 31, 17, 1), "0.30", "0.35", "0")
    add(M2, at(6, 1, 9, 0), "0.50", "0.55", "100")
    add(M2, at(6, 1, 9, 1), "0.50", "0.55", "0")
    # test 3: 06-01 10:00 buy, 10:10 sell
    add(M1, at(6, 1, 10, 0), "0.40", "0.45", "100")
    add(M1, at(6, 1, 10, 1), "0.40", "0.45", "0")
    add(M1, at(6, 1, 10, 10), "0.42", "0.47", "100")
    add(M1, at(6, 1, 10, 11), "0.44", "0.49", "0")
    # test 4: 06-01 12:00
    add(M1, at(6, 1, 12, 0), "0.50", "0.55", "100")
    add(M1, at(6, 1, 12, 1), "0.50", "0.55", "0")
    # test 10: 06-01 14:00 on M1 and M2
    for market in (M1, M2):
        add(market, at(6, 1, 14, 0), "0.40", "0.45", "100")
        add(market, at(6, 1, 14, 1), "0.40", "0.45", "0")
    # test 13: 06-01 16:00 on M1, M2, M3, M6; M6 also 16:10
    for market in (M1, M2, M3, M6):
        add(market, at(6, 1, 16, 0), "0.40", "0.45", "100")
        add(market, at(6, 1, 16, 1), "0.40", "0.45", "0")
    add(M6, at(6, 1, 16, 10), "0.40", "0.45", "100")
    add(M6, at(6, 1, 16, 11), "0.40", "0.45", "0")
    # test 9: 06-01 19:58 to 20:00, lock at 20:00
    add(M1, at(6, 1, 19, 58), "0.50", "0.55", "100")
    add(M1, at(6, 1, 19, 59), "0.50", "0.55", "100")
    add(M1, at(6, 1, 20, 0), "0.50", "0.55", "100")
    # test 22: M5 06-01 16:30, small volume up to T, big volume at T+1
    add(M5, at(6, 1, 16, 30), "0.50", "0.55", "30")
    add(M5, at(6, 1, 16, 31), "0.50", "0.55", "1000")
    # test 23: M5 06-01 18:00 buy no, 18:10 sell no
    add(M5, at(6, 1, 18, 0), "0.60", "0.64", "100")
    add(M5, at(6, 1, 18, 1), "0.62", "0.66", "0")
    add(M5, at(6, 1, 18, 10), "0.58", "0.63", "100")
    add(M5, at(6, 1, 18, 11), "0.58", "0.63", "0")
    # test 24a: M5 06-01 13:00 buy, 13:10 sell more than held
    add(M5, at(6, 1, 13, 0), "0.50", "0.55", "100")
    add(M5, at(6, 1, 13, 1), "0.50", "0.55", "0")
    add(M5, at(6, 1, 13, 10), "0.48", "0.53", "100")
    add(M5, at(6, 1, 13, 11), "0.48", "0.53", "0")
    # test 24b: M5 06-01 14:00 buy 10, 15:00 sell with window volume 45 (cap 4)
    add(M5, at(6, 1, 14, 0), "0.50", "0.55", "100")
    add(M5, at(6, 1, 14, 1), "0.50", "0.55", "0")
    add(M5, at(6, 1, 15, 0), "0.48", "0.53", "45")
    add(M5, at(6, 1, 15, 1), "0.48", "0.53", "0")
    # tests 25 and 26: own events with late locks; candles around the lock or the close
    add(M7, at(6, 10, 11, 59), "0.40", "0.45", "100")
    add(M7, at(6, 10, 12, 0), "0.40", "0.45", "100")
    for market, day, hour, minute in [(M8, 11, 16, 58), (M9, 12, 4, 58), (M10, 14, 3, 58)]:
        for step in range(3):
            add(market, at(6, day, hour, minute) + timedelta(minutes=step), "0.40", "0.45", "100")
    return rows


def load_fixture(conn):
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO station (station_id, name, latitude, longitude, tz_name, std_utc_offset_h,"
        " iem_id, iem_network) VALUES ('KTST', 'Test station', 40, -74, 'America/New_York', -5,"
        " 'TST', 'NY_ASOS')")
    cur.execute("INSERT INTO underlying (underlying_id, description, station_id)"
                " VALUES ('U1', 'Test city', 'KTST')")
    cur.execute("INSERT INTO series (ticker, underlying_id, title) VALUES ('S1', 'U1', 'Test series')")
    cur.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                " VALUES ('EV-0601', 'S1', 'U1', '2026-06-01', 'NWS'),"
                "        ('EV-0602', 'S1', 'U1', '2026-06-02', 'NWS')")
    open_time = at(5, 31, 15, 0)
    close_time = at(6, 2, 6, 0)
    markets = [
        (M1, "EV-0601", "yes", SETTLED_AT),
        (M2, "EV-0601", "no", SETTLED_AT),
        (M3, "EV-0601", None, None),
        (M5, "EV-0601", None, None),
        (M6, "EV-0601", "no", SETTLED_AT),
        (M4, "EV-0602", None, None),
    ]
    for ticker, event, result, settled_at in markets:
        cur.execute("INSERT INTO market (ticker, event_ticker, open_time, close_time, result, settled_at)"
                    " VALUES (%s, %s, %s, %s, %s, %s)",
                    (ticker, event, open_time, close_time, result, settled_at))
    # extra events for tests 25 and 26: (event, date, market, high time text, open, close)
    extra = [
        ("EV-0610", "2026-06-10", M7, "1100 PM", at(6, 9, 0, 0), at(6, 10, 12, 0)),
        ("EV-0611", "2026-06-11", M8, "1200 PM", at(6, 10, 0, 0), at(6, 12, 0, 0)),
        ("EV-0612", "2026-06-12", M9, "1230 AM", at(6, 11, 0, 0), at(6, 13, 0, 0)),
        ("EV-0613", "2026-06-13", M10, "1100 PM", at(6, 12, 0, 0), at(6, 15, 0, 0)),
    ]
    for event, event_date, market, high_time, market_open, market_close in extra:
        cur.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source)"
                    " VALUES (%s, 'S1', 'U1', %s, 'NWS')", (event, event_date))
        cur.execute("INSERT INTO market (ticker, event_ticker, open_time, close_time)"
                    " VALUES (%s, %s, %s, %s)", (market, event, market_open, market_close))
        cur.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst,"
                    " low_f, issued_at, reference)"
                    " VALUES ('KTST', %s, 'nws_cli', 80, %s, 60, %s, 'CLITEST')",
                    (event_date, high_time, at(6, 20, 0, 0)))
    # high time 3:00 PM local standard time on 06-01 -> 20:00 UTC -> lock 20:00 UTC
    cur.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst,"
                " low_f, issued_at, reference)"
                " VALUES ('KTST', '2026-06-01', 'nws_cli', 80, '300 PM', 60, %s, 'CLITEST')",
                (at(6, 2, 8, 0),))
    cur.executemany("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                    " VALUES (%s, %s, %s, %s, %s)", candles())
    conn.commit()


# ---------------------------------------------------------------------------
# Reading back what the engine wrote
# ---------------------------------------------------------------------------

def outcomes_of(conn, run_id):
    """One list per intent (in intent order) of outcome tuples (in outcome order)."""
    cur = conn.cursor()
    cur.execute(
        "SELECT i.intent_id, o.outcome, o.reason_code, o.quantity, o.price, o.fee, o.occurred_at"
        " FROM order_intent i JOIN order_outcome o ON o.intent_id = i.intent_id"
        " WHERE i.run_id = %s ORDER BY i.intent_id, o.outcome_id", (run_id,))
    grouped = {}
    for intent_id, outcome, reason_code, quantity, price, fee, occurred_at in cur.fetchall():
        if outcome == "filled":
            reason_code = None  # the spec does not fix a reason_code for filled rows
        grouped.setdefault(intent_id, []).append(
            (outcome, reason_code, quantity, price, fee, occurred_at))
    return list(grouped.values())


def settlements_of(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT market_ticker, settled_at, result, net_units, payout FROM settlement"
                " WHERE run_id = %s ORDER BY market_ticker", (run_id,))
    return cur.fetchall()


def snapshots_of(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT as_of_date, market_ticker, net_units, avg_cost FROM position_snapshot"
                " WHERE run_id = %s ORDER BY as_of_date, market_ticker", (run_id,))
    return cur.fetchall()


def row_counts(conn):
    cur = conn.cursor()
    counts = {}
    for table in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


def check_equal(actual, expected, what):
    assert actual == expected, f"{what}: expected {expected}, got {actual}"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_01_buy_yes_at_ask(conn, ctx):
    # T = 05-31 16:00, quote at 16:01 is bid 0.40 / ask 0.45; window (15:00, 16:00] has volume 200,
    # cap = floor(0.10 * 200) = 20, so 5 fill at 0.45.
    # fee = ceil(0.07 * 5 * 0.45 * 0.55 * 100) / 100 = ceil(8.6625) / 100 = 0.09
    run_id = match_intents(conn, "t01", {}, [intent(at(5, 31, 16, 0), M1, "yes", "buy", 5, "r1")])
    ctx["run_defaults"] = run_id
    check_equal(outcomes_of(conn, run_id), [[filled(5, "0.45", "0.09", at(5, 31, 16, 1))]], "outcomes")
    cur = conn.cursor()
    cur.execute("SELECT decided_at, market_ticker, side, action, quantity, reason FROM order_intent"
                " WHERE run_id = %s", (run_id,))
    check_equal(cur.fetchall(), [(at(5, 31, 16, 0), M1, "yes", "buy", D(5), "r1")], "order_intent")


def test_02_buy_no_at_one_minus_bid(conn, ctx):
    # T = 06-01 08:00, quote at 08:01 is bid 0.62; buy no price = 1 - 0.62 = 0.38; cap 10, buy 5.
    # fee = ceil(0.07 * 5 * 0.38 * 0.62 * 100) / 100 = ceil(8.246) / 100 = 0.09
    # position -5 (NO), kept to settlement: M1 settled yes, payout 0.
    run_id = match_intents(conn, "t02", {}, [intent(at(6, 1, 8, 0), M1, "no", "buy", 5)])
    check_equal(outcomes_of(conn, run_id), [[filled(5, "0.38", "0.09", at(6, 1, 8, 1))]], "outcomes")
    check_equal(snapshots_of(conn, run_id), [(date(2026, 6, 1), M1, D(-5), D("0.38"))], "snapshots")
    check_equal(settlements_of(conn, run_id), [(M1, SETTLED_AT, "yes", D(-5), D(0))], "settlements")


def test_03_sell_part_of_yes(conn, ctx):
    # buy 8 yes at T = 10:00 -> ask at 10:01 = 0.45, fee ceil(0.07*8*0.45*0.55*100)/100 = ceil(13.86)/100 = 0.14
    # sell 3 yes at T = 10:10 -> bid at 10:11 = 0.44, window (09:10, 10:10] volume 200, cap 20,
    # fee ceil(0.07*3*0.44*0.56*100)/100 = ceil(5.1744)/100 = 0.06; position 8 - 3 = 5.
    run_id = match_intents(conn, "t03", {}, [intent(at(6, 1, 10, 0), M1, "yes", "buy", 8),
                                             intent(at(6, 1, 10, 10), M1, "yes", "sell", 3)])
    check_equal(outcomes_of(conn, run_id),
                [[filled(8, "0.45", "0.14", at(6, 1, 10, 1))],
                 [filled(3, "0.44", "0.06", at(6, 1, 10, 11))]], "outcomes")
    check_equal(snapshots_of(conn, run_id), [(date(2026, 6, 1), M1, D(5), D("0.45"))], "snapshots")
    check_equal(settlements_of(conn, run_id), [(M1, SETTLED_AT, "yes", D(5), D(5))], "settlements")


def test_04_sell_with_nothing_held(conn, ctx):
    # nothing held, so both sells are rejected; no fills, so no settlement and no snapshot
    run_id = match_intents(conn, "t04", {}, [intent(at(6, 1, 12, 0), M1, "yes", "sell", 2),
                                             intent(at(6, 1, 12, 0), M1, "no", "sell", 2)])
    rejected = refused("rejected", "nothing_to_sell", at(6, 1, 12, 1))
    check_equal(outcomes_of(conn, run_id), [[rejected], [rejected]], "outcomes")
    check_equal(settlements_of(conn, run_id), [], "settlements")
    check_equal(snapshots_of(conn, run_id), [], "snapshots")


def test_05_no_quote(conn, ctx):
    # M1 05-31 22:00: the quote at 22:01 has a NULL ask, so buy yes has no price.
    # M5: its first candle is 06-01 12:00, nothing exists at 10:01.
    run_id = match_intents(conn, "t05", {}, [intent(at(5, 31, 22, 0), M1, "yes", "buy", 3),
                                             intent(at(6, 1, 10, 0), M5, "yes", "buy", 3)])
    check_equal(outcomes_of(conn, run_id),
                [[refused("rejected", "no_quote", at(5, 31, 22, 1))],
                 [refused("rejected", "no_quote", at(6, 1, 10, 1))]], "outcomes")


def test_06_market_not_open(conn, ctx):
    # decided 14:59 -> fill 15:00 = open_time, which is not after open; decided 06-02 06:00 ->
    # fill 06:01 is after close_time 06:00.
    run_id = match_intents(conn, "t06", {}, [intent(at(5, 31, 14, 59), M1, "yes", "buy", 1),
                                             intent(at(6, 2, 6, 0), M1, "yes", "buy", 1)])
    check_equal(outcomes_of(conn, run_id),
                [[refused("rejected", "market_not_open", at(5, 31, 15, 0))],
                 [refused("rejected", "market_not_open", at(6, 2, 6, 1))]], "outcomes")


def test_07_volume_cap_partial(conn, ctx):
    # window (01:00, 02:00] holds 60.4 + 66.0 = 126.4; cap = floor(0.10 * 126.4) = floor(12.64) = 12.
    # request 20: 12 fill at the ask 0.47, then a rejected volume_cap row.
    # fee = ceil(0.07 * 12 * 0.47 * 0.53 * 100) / 100 = ceil(20.9244) / 100 = 0.21
    run_id = match_intents(conn, "t07", {}, [intent(at(6, 1, 2, 0), M1, "yes", "buy", 20)])
    fill_time = at(6, 1, 2, 1)
    check_equal(outcomes_of(conn, run_id),
                [[filled(12, "0.47", "0.21", fill_time), refused("rejected", "volume_cap", fill_time)]],
                "outcomes")


def test_08_no_volume_in_window(conn, ctx):
    # window (04:00, 05:00] has volume 0 (the 500 at 04:00 is outside) -> one rejected row only
    run_id = match_intents(conn, "t08", {}, [intent(at(6, 1, 5, 0), M1, "yes", "buy", 5)])
    check_equal(outcomes_of(conn, run_id),
                [[refused("rejected", "volume_cap", at(6, 1, 5, 1))]], "outcomes")


def test_09_lock_and_unknown_high_time(conn, ctx):
    # lock = 20:00 UTC. Buy decided 19:58 fills at 19:59 (before lock): ask 0.55, window volume 100,
    # fee ceil(0.07 * 1 * 0.55 * 0.45 * 100) / 100 = ceil(1.7325) / 100 = 0.02.
    # Buy decided 19:59 fills at 20:00 = lock -> blocked. A sell decided 20:30 is blocked too.
    run_id = match_intents(conn, "t09a", {}, [intent(at(6, 1, 19, 58), M1, "yes", "buy", 1),
                                              intent(at(6, 1, 19, 59), M1, "yes", "buy", 1),
                                              intent(at(6, 1, 20, 30), M1, "yes", "sell", 1)])
    check_equal(outcomes_of(conn, run_id),
                [[filled(1, "0.55", "0.02", at(6, 1, 19, 59))],
                 [refused("blocked", "after_daily_high", at(6, 1, 20, 0))],
                 [refused("blocked", "after_daily_high", at(6, 1, 20, 31))]], "locked outcomes")
    # EV-0602 has no nws_cli row
    run_id = match_intents(conn, "t09b", {}, [intent(at(6, 1, 10, 0), M4, "yes", "buy", 1)])
    check_equal(outcomes_of(conn, run_id),
                [[refused("blocked", "high_time_unknown", at(6, 1, 10, 1))]], "unknown outcomes")


def test_10_opposite_position(conn, ctx):
    # M1: buy yes 3 at ask 0.45 (fee ceil(5.1975)/100 = 0.06), then buy no -> blocked.
    # M2: buy no 3 at 1 - 0.40 = 0.60 (fee ceil(0.07*3*0.6*0.4*100)/100 = ceil(5.04)/100 = 0.06),
    # then buy yes -> blocked. The blocked rows occur at 14:11.
    run_id = match_intents(conn, "t10", {}, [intent(at(6, 1, 14, 0), M1, "yes", "buy", 3),
                                             intent(at(6, 1, 14, 0), M2, "no", "buy", 3),
                                             intent(at(6, 1, 14, 10), M1, "no", "buy", 2),
                                             intent(at(6, 1, 14, 10), M2, "yes", "buy", 2)])
    blocked = refused("blocked", "opposite_position", at(6, 1, 14, 11))
    check_equal(outcomes_of(conn, run_id),
                [[filled(3, "0.45", "0.06", at(6, 1, 14, 1))],
                 [filled(3, "0.60", "0.06", at(6, 1, 14, 1))],
                 [blocked], [blocked]], "outcomes")
    # a winning NO position pays the units: M2 net -3, result no
    check_equal(settlements_of(conn, run_id),
                [(M1, SETTLED_AT, "yes", D(3), D(3)), (M2, SETTLED_AT, "no", D(-3), D(3))],
                "settlements")


def test_11_quote_changes_to_t_plus_one(conn, ctx):
    # T = 18:00 quote ask 0.35; T+1 = 18:01 quote ask 0.38 -> price 0.38. Window volume 100, cap 10.
    # fee = ceil(0.07 * 3 * 0.38 * 0.62 * 100) / 100 = ceil(4.9476) / 100 = 0.05
    run_id = match_intents(conn, "t11", {}, [intent(at(5, 31, 18, 0), M1, "yes", "buy", 3)])
    check_equal(outcomes_of(conn, run_id), [[filled(3, "0.38", "0.05", at(5, 31, 18, 1))]], "outcomes")


def test_12_no_candle_at_t_plus_one(conn, ctx):
    # T = 20:00, no candle at 20:01; the last candle is 20:00 (ask 0.57), the 20:02 candle (0.65)
    # is later and must not be used. Window (19:00, 20:00] volume 200, cap 20.
    # fee = ceil(0.07 * 4 * 0.57 * 0.43 * 100) / 100 = ceil(6.8628) / 100 = 0.07
    run_id = match_intents(conn, "t12", {}, [intent(at(5, 31, 20, 0), M1, "yes", "buy", 4)])
    check_equal(outcomes_of(conn, run_id), [[filled(4, "0.57", "0.07", at(5, 31, 20, 1))]], "outcomes")


def test_13_settlement(conn, ctx):
    # buy yes at 16:00 (ask 0.45, cap 10): M1 6 (settles yes, payout 6), M2 4 (settles no, payout 0),
    # M3 3 (unsettled, no row), M6 5 then sell 5 at 16:10 (closed, no row).
    run_id = match_intents(conn, "t13", {}, [intent(at(6, 1, 16, 0), M1, "yes", "buy", 6),
                                             intent(at(6, 1, 16, 0), M2, "yes", "buy", 4),
                                             intent(at(6, 1, 16, 0), M3, "yes", "buy", 3),
                                             intent(at(6, 1, 16, 0), M6, "yes", "buy", 5),
                                             intent(at(6, 1, 16, 10), M6, "yes", "sell", 5)])
    ctx["run13"] = run_id
    for rows in outcomes_of(conn, run_id):
        check_equal([row[0] for row in rows], ["filled"], "every intent fills")
    check_equal(settlements_of(conn, run_id),
                [(M1, SETTLED_AT, "yes", D(6), D(6)), (M2, SETTLED_AT, "no", D(4), D(0))],
                "settlements")


def test_14_snapshots(conn, ctx):
    # M2: buy 4 at 05-31 17:00 (ask 0.35), buy 6 at 06-01 09:00 (ask 0.55).
    # end of 05-31: 4 at 0.35; end of 06-01: 10 at (4*0.35 + 6*0.55) / 10 = 4.7 / 10 = 0.47;
    # settled 06-02 12:00, so no row for 06-02.
    run_id = match_intents(conn, "t14", {}, [intent(at(5, 31, 17, 0), M2, "yes", "buy", 4),
                                             intent(at(6, 1, 9, 0), M2, "yes", "buy", 6)])
    check_equal(snapshots_of(conn, run_id),
                [(date(2026, 5, 31), M2, D(4), D("0.35")), (date(2026, 6, 1), M2, D(10), D("0.47"))],
                "snapshots")
    check_equal(settlements_of(conn, run_id), [(M2, SETTLED_AT, "no", D(10), D(0))], "settlements")


def test_15_fee_formula(conn, ctx):
    # 12 contracts at 0.47, rate 0.07: 0.07 * 12 * 0.47 * 0.53 = 0.209244; * 100 = 20.9244;
    # rounded up to 21; fee = 0.21
    run_id = match_intents(conn, "t15", {}, [intent(at(6, 1, 3, 0), M2, "yes", "buy", 12)])
    check_equal(outcomes_of(conn, run_id), [[filled(12, "0.47", "0.21", at(6, 1, 3, 1))]], "outcomes")


def test_16_unknown_market_raises(conn, ctx):
    before = row_counts(conn)
    try:
        match_intents(conn, "t16", {}, [intent(at(6, 1, 12, 0), M1, "yes", "buy", 1),
                                        intent(at(6, 1, 12, 5), "T-NOPE", "yes", "buy", 1)])
    except ValueError:
        pass
    else:
        raise AssertionError("no ValueError for an unknown market")
    check_equal(row_counts(conn), before, "row counts after the failed call")


def test_17_run_row(conn, ctx):
    cur = conn.cursor()
    cur.execute("SELECT strategy, params, code_version, starting_cash, date_start, date_end,"
                " status, finished_at FROM run WHERE run_id = %s", (ctx["run_defaults"],))
    strategy, params, code_version, starting_cash, date_start, date_end, status, finished_at = cur.fetchone()
    check_equal(status, "done", "status")
    assert finished_at is not None, "finished_at is NULL"
    check_equal(strategy, "t01", "strategy")
    check_equal(starting_cash, D(1000), "starting_cash")
    check_equal((date_start, date_end), (date(2026, 5, 31), date(2026, 5, 31)), "dates")
    assert code_version, "code_version is empty"
    defaults = {"fill_delay_minutes": D(1), "volume_window_minutes": D(60),
                "volume_share": D("0.10"), "fee_rate": D("0.07"), "starting_cash": D(1000)}
    check_equal(set(params), set(defaults), "params keys")
    for key, expected in defaults.items():
        assert key in params, f"params lacks {key}: {params}"
        check_equal(D(str(params[key])), expected, "params " + key)


def test_19_avg_cost_resets_when_flat(conn, ctx):
    # extra: M6 buy 4 at 0.45, sell 4 at bid 0.40 (flat), buy 2 at ask 0.65.
    # The snapshot at the end of 06-01 is 2 at 0.65, not a mix with the earlier 0.45.
    run_id = match_intents(conn, "t19", {}, [intent(at(6, 1, 6, 0), M6, "yes", "buy", 4),
                                             intent(at(6, 1, 6, 10), M6, "yes", "sell", 4),
                                             intent(at(6, 1, 6, 20), M6, "yes", "buy", 2)])
    check_equal(snapshots_of(conn, run_id), [(date(2026, 6, 1), M6, D(2), D("0.65"))], "snapshots")


def test_20_empty_intents_raise(conn, ctx):
    before = row_counts(conn)
    try:
        match_intents(conn, "t20", {}, [])
    except ValueError:
        pass
    else:
        raise AssertionError("no ValueError for an empty intents list")
    check_equal(row_counts(conn), before, "row counts after the failed call")


def test_21_held_unsettled_snapshots(conn, ctx):
    # M3 from test 13: 3 bought at 0.45, never settles; close_time is 06-02 06:00, so snapshots
    # for 06-01 and 06-02, and no settlement row.
    run_id = ctx["run13"]
    m3_snapshots = [row for row in snapshots_of(conn, run_id) if row[1] == M3]
    check_equal(m3_snapshots, [(date(2026, 6, 1), M3, D(3), D("0.45")),
                               (date(2026, 6, 2), M3, D(3), D("0.45"))], "M3 snapshots")
    assert M3 not in [row[0] for row in settlements_of(conn, run_id)], "M3 has a settlement row"


def test_22_volume_after_decision_not_counted(conn, ctx):
    # M5 T = 16:30: window (15:30, 16:30] has volume 30 -> cap 3. The 1000 at 16:31 (T+1) is ignored.
    # price = ask 0.55; fee = ceil(0.07 * 3 * 0.55 * 0.45 * 100) / 100 = ceil(5.1975) / 100 = 0.06
    run_id = match_intents(conn, "t22", {}, [intent(at(6, 1, 16, 30), M5, "yes", "buy", 10)])
    fill_time = at(6, 1, 16, 31)
    check_equal(outcomes_of(conn, run_id),
                [[filled(3, "0.55", "0.06", fill_time), refused("rejected", "volume_cap", fill_time)]],
                "outcomes")


def test_23_sell_no(conn, ctx):
    # buy no 6 at T = 18:00: bid at 18:01 is 0.62, price 0.38, fee ceil(0.07*6*0.38*0.62*100)/100 = ceil(9.8952)/100 = 0.10
    # sell no 2 at T = 18:10: ask at 18:11 is 0.63, price 1 - 0.63 = 0.37,
    # fee ceil(0.07*2*0.37*0.63*100)/100 = ceil(3.2634)/100 = 0.04; net -6 -> -4, avg cost stays 0.38.
    # M5 never settles: snapshots for 06-01 and 06-02 (close day).
    run_id = match_intents(conn, "t23", {}, [intent(at(6, 1, 18, 0), M5, "no", "buy", 6),
                                             intent(at(6, 1, 18, 10), M5, "no", "sell", 2)])
    check_equal(outcomes_of(conn, run_id),
                [[filled(6, "0.38", "0.10", at(6, 1, 18, 1))],
                 [filled(2, "0.37", "0.04", at(6, 1, 18, 11))]], "outcomes")
    check_equal(snapshots_of(conn, run_id), [(date(2026, 6, 1), M5, D(-4), D("0.38")),
                                             (date(2026, 6, 2), M5, D(-4), D("0.38"))], "snapshots")


def test_24_sell_larger_than_holding(conn, ctx):
    # a: hold 3 (buy at ask 0.55, fee ceil(5.1975)/100 = 0.06), sell 10 at T = 13:10 with cap 20:
    # fills 3 at bid 0.48 (fee ceil(0.07*3*0.48*0.52*100)/100 = ceil(5.2416)/100 = 0.06),
    # then a rejected nothing_to_sell row.
    run_id = match_intents(conn, "t24a", {}, [intent(at(6, 1, 13, 0), M5, "yes", "buy", 3),
                                              intent(at(6, 1, 13, 10), M5, "yes", "sell", 10)])
    sell_time = at(6, 1, 13, 11)
    check_equal(outcomes_of(conn, run_id),
                [[filled(3, "0.55", "0.06", at(6, 1, 13, 1))],
                 [filled(3, "0.48", "0.06", sell_time), refused("rejected", "nothing_to_sell", sell_time)]],
                "outcomes a")
    # b: hold 10 (ask 0.55, fee ceil(0.07*10*0.55*0.45*100)/100 = ceil(17.325)/100 = 0.18), sell 10 at
    # T = 15:00 where the window (14:00, 15:00] has volume 45, cap floor(4.5) = 4: fills 4 at bid 0.48
    # (fee ceil(0.07*4*0.48*0.52*100)/100 = ceil(6.9888)/100 = 0.07), then a rejected volume_cap row.
    run_id = match_intents(conn, "t24b", {}, [intent(at(6, 1, 14, 0), M5, "yes", "buy", 10),
                                              intent(at(6, 1, 15, 0), M5, "yes", "sell", 10)])
    sell_time = at(6, 1, 15, 1)
    check_equal(outcomes_of(conn, run_id),
                [[filled(10, "0.55", "0.18", at(6, 1, 14, 1))],
                 [filled(4, "0.48", "0.07", sell_time), refused("rejected", "volume_cap", sell_time)]],
                "outcomes b")


def test_25_fill_at_close_time(conn, ctx):
    # M7 closes 06-10 12:00; its lock is 06-11 04:00 (high time 11:00 PM), so the lock is not in play.
    # Decided 11:59 -> fill 12:00 = close_time: allowed, ask 0.45, volume 100, cap 10, buy 2,
    # fee ceil(0.07 * 2 * 0.45 * 0.55 * 100) / 100 = ceil(3.465) / 100 = 0.04.
    # Decided 12:00 -> fill 12:01 is after close.
    run_id = match_intents(conn, "t25", {}, [intent(at(6, 10, 11, 59), M7, "yes", "buy", 2),
                                             intent(at(6, 10, 12, 0), M7, "yes", "buy", 2)])
    check_equal(outcomes_of(conn, run_id),
                [[filled(2, "0.45", "0.04", at(6, 10, 12, 0))],
                 [refused("rejected", "market_not_open", at(6, 10, 12, 1))]], "outcomes")


def test_26_lock_time_conversion(conn, ctx):
    # offset -5: local time + 5 h = UTC, then cut to the top of the hour.
    # '1200 PM' on 06-11 -> 17:00 UTC 06-11; '1230 AM' on 06-12 -> 05:30 UTC -> lock 05:00 06-12;
    # '1100 PM' on 06-13 -> 04:00 UTC 06-14.
    cases = [(M8, at(6, 11, 17, 0)), (M9, at(6, 12, 5, 0)), (M10, at(6, 14, 4, 0))]
    for market, lock in cases:
        decided_early = lock - timedelta(minutes=2)   # fills one minute before the lock
        decided_late = lock - timedelta(minutes=1)    # fills exactly at the lock
        run_id = match_intents(conn, "t26", {}, [intent(decided_early, market, "yes", "buy", 1),
                                                 intent(decided_late, market, "yes", "buy", 1)])
        # price 0.45, fee ceil(0.07 * 0.45 * 0.55 * 100) / 100 = ceil(1.7325) / 100 = 0.02
        check_equal(outcomes_of(conn, run_id),
                    [[filled(1, "0.45", "0.02", lock - timedelta(minutes=1))],
                     [refused("blocked", "after_daily_high", lock)]], f"outcomes {market}")


def test_18_live_database_untouched(live_before):
    live_conn = connect()
    live_after = row_counts(live_conn)
    live_conn.close()
    check_equal(live_after, live_before, "live row counts")


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_test(name, function, *args):
    try:
        function(*args)
    except Exception as error:
        traceback.print_exc()
        report(name, False, f"{type(error).__name__}: {error}")
        try:
            args[0].rollback()
        except Exception:
            pass
    else:
        report(name, True)


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

        ctx = {}
        run_test("01 buy yes fills at the ask at T+1", test_01_buy_yes_at_ask, conn, ctx)
        run_test("02 buy no fills at 1 - bid, position negative", test_02_buy_no_at_one_minus_bid, conn, ctx)
        run_test("03 sell part of a yes holding at the bid", test_03_sell_part_of_yes, conn, ctx)
        run_test("04 sell with nothing held is rejected", test_04_sell_with_nothing_held, conn, ctx)
        run_test("05 NULL side and no candle give no_quote", test_05_no_quote, conn, ctx)
        run_test("06 before open and after close give market_not_open", test_06_market_not_open, conn, ctx)
        run_test("07 request above the cap fills the cap and rejects the rest", test_07_volume_cap_partial, conn, ctx)
        run_test("08 no volume in the window rejects with volume_cap", test_08_no_volume_in_window, conn, ctx)
        run_test("09 after the lock blocked; unknown high time blocked", test_09_lock_and_unknown_high_time, conn, ctx)
        run_test("10 opposite side while holding is blocked", test_10_opposite_position, conn, ctx)
        run_test("11 quote change between T and T+1 uses T+1", test_11_quote_changes_to_t_plus_one, conn, ctx)
        run_test("12 no candle at T+1 uses the previous candle", test_12_no_candle_at_t_plus_one, conn, ctx)
        run_test("13 settlement rows and payouts", test_13_settlement, conn, ctx)
        run_test("14 daily position snapshots", test_14_snapshots, conn, ctx)
        run_test("15 fee formula, 12 at 0.47 is 0.21", test_15_fee_formula, conn, ctx)
        run_test("16 unknown market raises and writes nothing", test_16_unknown_market_raises, conn, ctx)
        run_test("17 run row done, finished_at set, defaults in params", test_17_run_row, conn, ctx)
        run_test("18 live database row counts unchanged", test_18_live_database_untouched, live_before)
        run_test("19 (extra) avg cost resets when the position goes flat", test_19_avg_cost_resets_when_flat, conn, ctx)
        run_test("20 (extra) empty intents list raises", test_20_empty_intents_raise, conn, ctx)
        run_test("21 (extra) held unsettled market is snapshotted to its close day", test_21_held_unsettled_snapshots, conn, ctx)
        run_test("22 (extra) volume after the decision is not counted", test_22_volume_after_decision_not_counted, conn, ctx)
        run_test("23 (extra) sell no at 1 - ask", test_23_sell_no, conn, ctx)
        run_test("24 (extra) sell larger than the holding, and volume cap on a sell", test_24_sell_larger_than_holding, conn, ctx)
        run_test("25 (extra) fill at close_time allowed", test_25_fill_at_close_time, conn, ctx)
        run_test("26 (extra) lock time conversion", test_26_lock_time_conversion, conn, ctx)
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
