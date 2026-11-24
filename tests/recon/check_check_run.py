"""Checks the reconciliation script (spec 013, agreed tests 1 to 15).

The journal tables are append-only, so nothing here writes to the live database. The script
creates a throwaway database kalbt_recon_test, applies db/schema.sql, inserts a hand-made
fixture, and drops the database at the end. Every planted error is made in the throwaway
database only: the append-only triggers of one table are disabled, one change is made, the
triggers are enabled again. Every scenario builds its own fresh run, so nothing needs undoing.

Fixture: station KTST (offset -5), event EV-0601 on 2026-06-01, high time '300 PM' -> lock
2026-06-01 20:00 UTC. Kalshi settled high 80. Markets (open 05-31 15:00, close 06-02 06:00 UTC):
  M1   between 78-82, settled yes at 06-02 12:00   (consistent with the high)
  M2   between 60-65, settled no  at 06-02 12:00   (consistent with the high)
  M3   no strike, unsettled
  MBAD between 90-95, settled yes at 06-02 12:00   (contradicts the high of 80: expected no)

Clean run (7 intents, chronological), worked out by hand:
  a. 05-31 16:00 M1 buy yes 5: cap floor(0.1*200)=20, fills 5 at ask 0.45, fee 0.09
  b. 06-01 02:00 M2 buy yes 20: window volume 126.4, cap 12, fills 12 at ask 0.47 (fee 0.21),
     then a rejected volume_cap row
  c. 06-01 10:00 M1 buy yes 8: fills 8 at ask 0.45, fee 0.14
  d. 06-01 10:10 M1 sell yes 3: fills 3 at bid 0.44, fee 0.06   (early exit; M1 net 5+8-3 = 10)
  e. 06-01 12:00 M3 sell yes 2: nothing held, rejected nothing_to_sell
  f. 06-01 16:00 M3 buy yes 3: fills 3 at ask 0.45, fee 0.06 (held, M3 never settles)
  g. 06-01 20:30 M1 buy yes 1: fill time 20:31 is after the lock, blocked after_daily_high
Settlements: M1 yes net 10 payout 10; M2 no net 12 payout 0.
Snapshots: M1 05-31 (5, 0.45) and 06-01 (10, 0.45); M2 06-01 (12, 0.47);
           M3 06-01 and 06-02 (3, 0.45), 06-02 being the close day.
"""
import traceback
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from psycopg.types.json import Jsonb

from db.connect import connect
from engine.match_intents import match_intents
from recon.check_run import check_run

SCHEMA = Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql"
TEST_DB = "kalbt_recon_test"
TABLES = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]

D = Decimal
M1, M2, M3, MBAD = "T-M1", "T-M2", "T-M3", "T-MBAD"
SETTLED_AT = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
ALL_CHECKS = set(range(1, 14))
PARAMS = {"fill_delay_minutes": 1, "volume_window_minutes": 60, "volume_share": "0.10",
          "fee_rate": "0.07", "starting_cash": "1000"}

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def at(month, day, hour, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


def intent(decided_at, ticker, side, action, quantity):
    return {"decided_at": decided_at, "market_ticker": ticker, "side": side,
            "action": action, "quantity": quantity, "reason": None}


# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

def candles():
    """(market, ts, yes_bid, yes_ask, volume)."""
    rows = []

    def add(market, ts, bid, ask, volume):
        rows.append((market, ts, D(bid), D(ask), D(volume)))

    add(M1, at(5, 31, 16, 0), "0.40", "0.45", "200")
    add(M1, at(5, 31, 16, 1), "0.40", "0.45", "0")
    add(M1, at(6, 1, 10, 0), "0.40", "0.45", "100")
    add(M1, at(6, 1, 10, 1), "0.40", "0.45", "0")
    add(M1, at(6, 1, 10, 10), "0.42", "0.47", "100")
    add(M1, at(6, 1, 10, 11), "0.44", "0.49", "0")
    add(M1, at(6, 1, 19, 58), "0.50", "0.55", "100")
    add(M1, at(6, 1, 19, 59), "0.50", "0.55", "100")
    add(M1, at(6, 1, 20, 0), "0.50", "0.55", "100")   # the lock
    add(M2, at(6, 1, 1, 30), "0.45", "0.47", "60.4")
    add(M2, at(6, 1, 2, 0), "0.45", "0.47", "66.0")
    add(M2, at(6, 1, 2, 1), "0.45", "0.47", "0")
    add(M3, at(6, 1, 12, 0), "0.40", "0.45", "100")
    add(M3, at(6, 1, 12, 1), "0.40", "0.45", "0")
    add(M3, at(6, 1, 16, 0), "0.40", "0.45", "100")
    add(M3, at(6, 1, 16, 1), "0.40", "0.45", "0")
    add(M3, at(6, 1, 16, 10), "0.42", "0.47", "100")
    add(M3, at(6, 1, 16, 11), "0.42", "0.47", "0")
    add(MBAD, at(6, 1, 3, 0), "0.45", "0.47", "100")
    add(MBAD, at(6, 1, 3, 1), "0.45", "0.47", "0")
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
                " VALUES ('EV-0601', 'S1', 'U1', '2026-06-01', 'NWS')")
    open_time = at(5, 31, 15, 0)
    close_time = at(6, 2, 6, 0)
    # ticker, strike_type, floor, cap, result, settled_at
    markets = [
        (M1, "between", 78, 82, "yes", SETTLED_AT),
        (M2, "between", 60, 65, "no", SETTLED_AT),
        (M3, None, None, None, None, None),
        (MBAD, "between", 90, 95, "yes", SETTLED_AT),
    ]
    for ticker, strike_type, floor_strike, cap_strike, result, settled_at in markets:
        cur.execute("INSERT INTO market (ticker, event_ticker, strike_type, floor_strike, cap_strike,"
                    " open_time, close_time, result, settled_at)"
                    " VALUES (%s, 'EV-0601', %s, %s, %s, %s, %s, %s, %s)",
                    (ticker, strike_type, floor_strike, cap_strike, open_time, close_time,
                     result, settled_at))
    cur.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst,"
                " low_f, issued_at, reference)"
                " VALUES ('KTST', '2026-06-01', 'nws_cli', 80, '300 PM', 60, %s, 'CLITEST')",
                (at(6, 2, 8, 0),))
    cur.execute("INSERT INTO official_daily (station_id, climate_date, source, high_f, issued_at,"
                " reference) VALUES ('KTST', '2026-06-01', 'kalshi', 80, %s, 'EV-0601')",
                (at(6, 2, 12, 0),))
    cur.executemany("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume)"
                    " VALUES (%s, %s, %s, %s, %s)", candles())
    conn.commit()


# ---------------------------------------------------------------------------
# Building runs
# ---------------------------------------------------------------------------

def clean_run(conn):
    """One engine run with a full fill, a partial fill, a rejection, a block, an early exit,
    a position held to settlement, and snapshots."""
    return match_intents(conn, "recon", {}, [
        intent(at(5, 31, 16, 0), M1, "yes", "buy", 5),
        intent(at(6, 1, 2, 0), M2, "yes", "buy", 20),
        intent(at(6, 1, 10, 0), M1, "yes", "buy", 8),
        intent(at(6, 1, 10, 10), M1, "yes", "sell", 3),
        intent(at(6, 1, 12, 0), M3, "yes", "sell", 2),
        intent(at(6, 1, 16, 0), M3, "yes", "buy", 3),
        intent(at(6, 1, 20, 30), M1, "yes", "buy", 1),
    ])


def filled(quantity, price, fee, occurred_at):
    return ("filled", D(quantity), D(price), D(fee), occurred_at, None)


def refused(outcome, reason_code, occurred_at):
    return (outcome, D(0), None, None, occurred_at, reason_code)


def hand_run(conn, entries, snapshots=(), settlements=()):
    """Insert a run by hand. entries: (decided_at, market, side, action, quantity, [outcomes]),
    snapshots: (date, market, net, avg_cost), settlements: (market, result, net, payout)."""
    cur = conn.cursor()
    cur.execute("INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end,"
                " status, finished_at) VALUES ('hand', %s, 'test', 1000, '2026-05-31', '2026-06-02',"
                " 'done', now()) RETURNING run_id", (Jsonb(PARAMS),))
    run_id = cur.fetchone()[0]
    for decided_at, market, side, action, quantity, outcomes in entries:
        cur.execute("INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity)"
                    " VALUES (%s, %s, %s, %s, %s, %s) RETURNING intent_id",
                    (run_id, decided_at, market, side, action, quantity))
        intent_id = cur.fetchone()[0]
        for outcome, out_quantity, price, fee, occurred_at, reason_code in outcomes:
            cur.execute("INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price,"
                        " fee, reason_code) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                        (intent_id, occurred_at, outcome, out_quantity, price, fee, reason_code))
    for as_of_date, market, net, avg_cost in snapshots:
        cur.execute("INSERT INTO position_snapshot (run_id, as_of_date, market_ticker, net_units, avg_cost)"
                    " VALUES (%s, %s, %s, %s, %s)", (run_id, as_of_date, market, net, avg_cost))
    for market, result, net, payout in settlements:
        cur.execute("INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout)"
                    " VALUES (%s, %s, %s, %s, %s, %s)", (run_id, market, SETTLED_AT, result, net, payout))
    conn.commit()
    return run_id


# ---------------------------------------------------------------------------
# Helpers for planting errors and reading results
# ---------------------------------------------------------------------------

def tamper(conn, table, sql, params):
    """Change one thing in the throwaway database, with the table's triggers off."""
    cur = conn.cursor()
    cur.execute(f"ALTER TABLE {table} DISABLE TRIGGER USER")
    cur.execute(sql, params)
    assert cur.rowcount == 1, f"tamper changed {cur.rowcount} rows, expected 1"
    cur.execute(f"ALTER TABLE {table} ENABLE TRIGGER USER")
    conn.commit()


def intent_id_of(conn, run_id, market, action, decided_at):
    cur = conn.cursor()
    cur.execute("SELECT intent_id FROM order_intent WHERE run_id = %s AND market_ticker = %s"
                " AND action = %s AND decided_at = %s", (run_id, market, action, decided_at))
    return cur.fetchone()[0]


def failing(result):
    return {check["id"] for check in result["checks"] if check["status"] == "fail"}


def show(result):
    lines = [f'{c["id"]} {c["name"]} {c["status"]}: {c["detail"]} {c["examples"][:2]}'
             for c in result["checks"] if c["status"] == "fail"]
    return "; ".join(lines)


def expect_fail(conn, run_id, expected_ids):
    """Verdict FAIL and exactly the expected checks failing."""
    result = check_run(conn, run_id)
    assert result["verdict"] == "FAIL", f"verdict {result['verdict']}, expected FAIL"
    actual = failing(result)
    assert actual == set(expected_ids), \
        f"failing checks {sorted(actual)}, expected {sorted(expected_ids)}: {show(result)}"
    return result


def row_counts(conn):
    cur = conn.cursor()
    counts = {}
    for table in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_01_clean_run(conn, ctx):
    run_id = clean_run(conn)
    ctx["clean"] = run_id
    result = check_run(conn, run_id)
    assert result["verdict"] == "PASS", f"verdict {result['verdict']}: {show(result)}"
    assert result["run_id"] == run_id
    assert [c["id"] for c in result["checks"]] == list(range(1, 14)), "check ids"
    assert all(c["status"] == "pass" for c in result["checks"]), show(result)
    assert all(len(c["examples"]) <= 10 for c in result["checks"])
    # 7 intents; 5 filled rows (a, b, c, d, f); rejected rows (b volume_cap, e nothing_to_sell) = 2;
    # blocked (g) = 1; settlements M1 and M2 = 2; snapshots M1 x2, M2 x1, M3 x2 = 5
    expected = {"intents": 7, "filled": 5, "rejected": 2, "blocked": 1,
                "settlements": 2, "snapshots": 5}
    assert result["counts"] == expected, f"counts {result['counts']}, expected {expected}"


def test_02_missing_outcome(conn, ctx):
    run_id = clean_run(conn)
    blocked_intent = intent_id_of(conn, run_id, M1, "buy", at(6, 1, 20, 30))
    tamper(conn, "order_outcome", "DELETE FROM order_outcome WHERE intent_id = %s", (blocked_intent,))
    expect_fail(conn, run_id, {1})


def test_03_outcome_time_moved(conn, ctx):
    run_id = clean_run(conn)
    blocked_intent = intent_id_of(conn, run_id, M1, "buy", at(6, 1, 20, 30))
    tamper(conn, "order_outcome",
           "UPDATE order_outcome SET occurred_at = occurred_at + interval '1 minute'"
           " WHERE intent_id = %s", (blocked_intent,))
    expect_fail(conn, run_id, {2})


def test_04_overfill(conn, ctx):
    # intent a: buy 5, fill raised to 6. Also expected to fail because of the same change:
    # 8 (fee for 6 at 0.45 is 0.11, stored 0.09), 11 (M1 snapshots show 5 and 10, replay 6 and 11),
    # 12 (M1 settlement net 10, replay 11).
    run_id = clean_run(conn)
    intent_a = intent_id_of(conn, run_id, M1, "buy", at(5, 31, 16, 0))
    tamper(conn, "order_outcome", "UPDATE order_outcome SET quantity = 6 WHERE intent_id = %s", (intent_a,))
    expect_fail(conn, run_id, {3, 8, 11, 12})


def test_05_mid_price(conn, ctx):
    # intent a: fill price 0.45 -> mid (0.40 + 0.45) / 2 = 0.425. The fee for 5 at 0.425 is
    # ceil(8.55) = 0.09, unchanged. The M1 snapshot avg_cost is built from the stored fill prices, so
    # check 11 also fails (replay 0.425, then (5*0.425 + 8*0.45) / 13; snapshot stored 0.45).
    run_id = clean_run(conn)
    intent_a = intent_id_of(conn, run_id, M1, "buy", at(5, 31, 16, 0))
    tamper(conn, "order_outcome", "UPDATE order_outcome SET price = 0.425 WHERE intent_id = %s", (intent_a,))
    expect_fail(conn, run_id, {4, 11})


def test_06_fill_at_lock(conn, ctx):
    # hand-made run: M1 buy 1 decided 19:59, filled 20:00 = lock at ask 0.55, fee ceil(1.7325) = 0.02,
    # window volume 200 (cap 20). Snapshot and settlement are inserted consistently, so only 6 fails.
    run_id = hand_run(
        conn,
        [(at(6, 1, 19, 59), M1, "yes", "buy", 1, [filled(1, "0.55", "0.02", at(6, 1, 20, 0))])],
        snapshots=[(date(2026, 6, 1), M1, D(1), D("0.55"))],
        settlements=[(M1, "yes", D(1), D(1))])
    result = expect_fail(conn, run_id, {6})
    assert result["counts"]["filled"] == 1


def test_07_over_volume_cap(conn, ctx):
    # intent b: buy 20, cap 12, fill raised 12 -> 15 (not above 20). Also expected to fail:
    # 8 (fee for 15 at 0.47 is 0.27, stored 0.21), 10 (the second row, volume_cap, requires the fill to
    # equal the cap of 12), 11 (M2 snapshot 12, replay 15), 12 (M2 settlement net 12, replay 15).
    run_id = clean_run(conn)
    intent_b = intent_id_of(conn, run_id, M2, "buy", at(6, 1, 2, 0))
    tamper(conn, "order_outcome",
           "UPDATE order_outcome SET quantity = 15 WHERE intent_id = %s AND outcome = 'filled'",
           (intent_b,))
    expect_fail(conn, run_id, {7, 8, 10, 11, 12})


def test_08_fee_one_cent_lower(conn, ctx):
    run_id = clean_run(conn)
    intent_a = intent_id_of(conn, run_id, M1, "buy", at(5, 31, 16, 0))
    tamper(conn, "order_outcome", "UPDATE order_outcome SET fee = 0.08 WHERE intent_id = %s", (intent_a,))
    expect_fail(conn, run_id, {8})


def test_09_sell_more_than_held(conn, ctx):
    # hand-made run on M3: buy 3 (filled 16:01 at 0.45, fee 0.06), then sell 5 filled at 16:11 at the
    # bid 0.42 (fee ceil(8.526) = 0.09, window volume 200, cap 20): held is 3, so it is a naked short.
    # The snapshots are inserted with net -2 and avg_cost 0.45 (the spec does not fix the average cost
    # of a negative position), so check 11 is left unasserted; every other check except 9 must pass.
    run_id = hand_run(
        conn,
        [(at(6, 1, 16, 0), M3, "yes", "buy", 3, [filled(3, "0.45", "0.06", at(6, 1, 16, 1))]),
         (at(6, 1, 16, 10), M3, "yes", "sell", 5, [filled(5, "0.42", "0.09", at(6, 1, 16, 11))])],
        snapshots=[(date(2026, 6, 1), M3, D(-2), D("0.45")), (date(2026, 6, 2), M3, D(-2), D("0.45"))])
    result = check_run(conn, run_id)
    assert result["verdict"] == "FAIL", "verdict is not FAIL"
    statuses = {c["id"]: c["status"] for c in result["checks"]}
    assert statuses[9] == "fail", f"check 9 is {statuses[9]}: {show(result)}"
    others = {i for i in ALL_CHECKS - {9, 11} if statuses[i] == "fail"}
    assert not others, f"unexpected failing checks {sorted(others)}: {show(result)}"


def test_10_no_quote_where_quote_exists(conn, ctx):
    # hand-made run on M3: a valid buy 3 (filled 16:01 at 0.45, fee 0.06, snapshots 06-01 and 06-02),
    # then a buy decided 16:10 rejected as no_quote at 16:11, where the candle at 16:11 has ask 0.47.
    run_id = hand_run(
        conn,
        [(at(6, 1, 16, 0), M3, "yes", "buy", 3, [filled(3, "0.45", "0.06", at(6, 1, 16, 1))]),
         (at(6, 1, 16, 10), M3, "yes", "buy", 1, [refused("rejected", "no_quote", at(6, 1, 16, 11))])],
        snapshots=[(date(2026, 6, 1), M3, D(3), D("0.45")), (date(2026, 6, 2), M3, D(3), D("0.45"))])
    expect_fail(conn, run_id, {10})


def test_11_snapshot_changed_or_deleted(conn, ctx):
    run_id = clean_run(conn)
    tamper(conn, "position_snapshot",
           "UPDATE position_snapshot SET net_units = 4 WHERE run_id = %s AND market_ticker = %s"
           " AND as_of_date = '2026-06-02'", (run_id, M3))
    expect_fail(conn, run_id, {11})
    run_id = clean_run(conn)
    tamper(conn, "position_snapshot",
           "DELETE FROM position_snapshot WHERE run_id = %s AND market_ticker = %s"
           " AND as_of_date = '2026-06-01'", (run_id, M1))
    expect_fail(conn, run_id, {11})


def test_12_settlement_deleted_or_changed(conn, ctx):
    run_id = clean_run(conn)
    tamper(conn, "settlement", "DELETE FROM settlement WHERE run_id = %s AND market_ticker = %s",
           (run_id, M1))
    expect_fail(conn, run_id, {12})
    run_id = clean_run(conn)
    tamper(conn, "settlement", "UPDATE settlement SET payout = 9 WHERE run_id = %s AND market_ticker = %s",
           (run_id, M1))
    expect_fail(conn, run_id, {12})


def test_13_result_contradicts_bracket(conn, ctx):
    # MBAD is between 90 and 95 and settled yes, but the Kalshi settled high is 80: expected no.
    # buy 1 decided 03:00: ask 0.47 at 03:01, fee ceil(1.7435) = 0.02; settles yes, payout 1.
    run_id = match_intents(conn, "recon13", {}, [intent(at(6, 1, 3, 0), MBAD, "yes", "buy", 1)])
    result = expect_fail(conn, run_id, {13})
    assert any(MBAD in example for check in result["checks"] if check["id"] == 13
               for example in check["examples"]), "no example names the market"


def test_14_unknown_run_raises(conn, ctx):
    try:
        check_run(conn, 999999)
    except ValueError:
        pass
    else:
        raise AssertionError("no ValueError for an unknown run_id")


def test_15_read_only_and_live_untouched(live_before, ctx):
    # the checker runs on a read-only connection to the throwaway database; any non-SELECT statement
    # would raise there
    ro_conn = connect(dbname=TEST_DB)
    try:
        ro_conn.read_only = True
        result = check_run(ro_conn, ctx["clean"])
        assert result["verdict"] == "PASS", f"read-only run: {show(result)}"
    finally:
        ro_conn.close()
    live_conn = connect()
    try:
        live_after = row_counts(live_conn)
    finally:
        live_conn.close()
    assert live_after == live_before, f"live row counts: before {live_before}, after {live_after}"


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run_test(name, function, conn, ctx):
    try:
        function(conn, ctx)
    except Exception as error:
        traceback.print_exc()
        report(name, False, f"{type(error).__name__}: {error}")
        try:
            conn.rollback()
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
        run_test("01 clean run passes all 13 checks", test_01_clean_run, conn, ctx)
        run_test("02 missing outcome fails check 1", test_02_missing_outcome, conn, ctx)
        run_test("03 outcome time moved fails check 2", test_03_outcome_time_moved, conn, ctx)
        run_test("04 overfill fails check 3", test_04_overfill, conn, ctx)
        run_test("05 mid-price fill fails check 4", test_05_mid_price, conn, ctx)
        run_test("06 fill at the lock fails check 6", test_06_fill_at_lock, conn, ctx)
        run_test("07 fill above the volume cap fails check 7", test_07_over_volume_cap, conn, ctx)
        run_test("08 fee one cent lower fails check 8", test_08_fee_one_cent_lower, conn, ctx)
        run_test("09 sell above holding fails check 9", test_09_sell_more_than_held, conn, ctx)
        run_test("10 no_quote where a quote exists fails check 10", test_10_no_quote_where_quote_exists, conn, ctx)
        run_test("11 snapshot changed and deleted fail check 11", test_11_snapshot_changed_or_deleted, conn, ctx)
        run_test("12 settlement deleted and changed fail check 12", test_12_settlement_deleted_or_changed, conn, ctx)
        run_test("13 result contradicting the bracket fails check 13", test_13_result_contradicts_bracket, conn, ctx)
        run_test("14 unknown run_id raises ValueError", test_14_unknown_run_raises, conn, ctx)
        if "clean" in ctx:
            run_test("15 read-only connection works, live counts unchanged",
                     lambda c, x: test_15_read_only_and_live_untouched(live_before, x), conn, ctx)
        else:
            report("15 read-only connection works, live counts unchanged", False, "test 1 gave no clean run")
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
