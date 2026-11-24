"""Checks for spec A022 (query/market.py, query/weather.py), agreed tests 1 to 9.

Run from the repo root: uv run python -m tests.query.check_queries
Read-only on live kalbt, fixed events only (a backfill may be running, so no whole-table counts).
"""
import datetime as dt
import sys
from pathlib import Path
import traceback
from decimal import Decimal

import psycopg
from psycopg.rows import dict_row

from db.connect import connect
from query import market, weather

NY = "KXHIGHNY-26NOV22"
CHI = "KXHIGHCHI-26NOV22"
UTC = dt.timezone.utc
SCHEMA = Path(__file__).resolve().parent.parent.parent / "db" / "schema.sql"
TEST_DB = "kalbt_a022_test"
TRADING = ["run", "order_intent", "order_outcome", "settlement", "position_snapshot"]
STEP = dt.timedelta(minutes=15)

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + str(detail) if detail and not ok else ""))
    if not ok:
        failures.append(name)


def utc(y, mo, d, h=0):
    return dt.datetime(y, mo, d, h, tzinfo=UTC)


def ceil_to_grid(ts, step_s):
    """Smallest grid point (multiples of step_s since the epoch) at or after ts."""
    micro = (ts - dt.datetime(1970, 1, 1, tzinfo=UTC)) // dt.timedelta(microseconds=1)
    step_us = step_s * 1_000_000
    return -(-micro // step_us) * step_us // 1_000_000


def test_1_cities(conn):
    rows = market.cities(conn)
    by_series = {r["series_ticker"]: r for r in rows}
    assert by_series["KXHIGHNY"]["underlying_id"] == "NYC_HIGH"
    assert by_series["KXHIGHNY"]["station_id"] == "KNYC"
    assert by_series["KXHIGHCHI"]["underlying_id"] == "MDW_HIGH"
    assert by_series["KXHIGHCHI"]["station_id"] == "KMDW"
    assert all(r["station_id"] for r in rows), "row without station"
    assert {"underlying_id", "description", "station_id", "series_ticker"} <= set(rows[0])


def test_2_event_days(conn):
    rows = market.event_days(conn, "NYC_HIGH")
    by_date = {r["event_date"]: r for r in rows}
    for day in range(16, 23):
        d = dt.date(2026, 11, day)
        assert d in by_date, f"{d} missing"
        assert by_date[d]["n_markets"] == 6, (d, by_date[d]["n_markets"])
        assert by_date[d]["settled"] is True, (d, by_date[d]["settled"])
    assert by_date[dt.date(2026, 11, 22)]["event_ticker"] == NY
    dates = [r["event_date"] for r in rows]
    assert dates == sorted(dates), "not ordered by date"


def test_3_ladder(conn):
    rows = market.ladder(conn, NY)
    assert len(rows) == 6, len(rows)
    assert [r["ladder_pos"] for r in rows] == [1, 2, 3, 4, 5, 6]
    yes = [r for r in rows if r["result"] == "yes"]
    assert len(yes) == 1, len(yes)
    high = yes[0]["settled_high_f"]
    assert high == 61, high
    lo, hi = yes[0]["temp_lo"], yes[0]["temp_hi"]
    assert (lo is None or lo <= high) and (hi is None or high <= hi), (lo, hi, high)


def test_4_quotes(conn):
    rows = market.quotes(conn, NY, 15)
    with conn.cursor(row_factory=dict_row) as cur:
        cur.execute(
            "SELECT c.market_ticker, c.ts, c.yes_bid, c.yes_ask, c.volume FROM candle c "
            "JOIN market m ON m.ticker = c.market_ticker WHERE m.event_ticker = %s ORDER BY c.ts",
            (NY,),
        )
        raw = cur.fetchall()
        cur.execute("SELECT ticker FROM market WHERE event_ticker = %s", (NY,))
        tickers = {r["ticker"] for r in cur.fetchall()}
    assert len(tickers) == 6
    candles = {t: [c for c in raw if c["market_ticker"] == t] for t in tickers}
    assert {r["market_ticker"] for r in rows} == tickers, "market set differs"

    # step grid: independent expectation from epoch arithmetic
    t0 = min(c["ts"] for c in raw)
    t1 = max(c["ts"] for c in raw)
    step_s = 15 * 60
    first = ceil_to_grid(t0, step_s)
    last = ceil_to_grid(t1, step_s)
    expected_steps = [dt.datetime.fromtimestamp(s, UTC) for s in range(first, last + 1, step_s)]
    assert expected_steps[0] - STEP < t0 <= expected_steps[0]
    for t in tickers:
        steps = [r["ts"] for r in rows if r["market_ticker"] == t]
        assert steps == expected_steps, f"{t}: steps differ, got {len(steps)} expected {len(expected_steps)}"
    for r in rows:
        assert r["ts"].minute in (0, 15, 30, 45) and r["ts"].second == 0, r["ts"]
    assert [(r["market_ticker"], r["ts"]) for r in rows] == sorted((r["market_ticker"], r["ts"]) for r in rows)

    n_quoted = 0
    for r in rows:
        cs = candles[r["market_ticker"]]
        before = [c for c in cs if c["ts"] <= r["ts"]]
        if before:
            last_c = max(before, key=lambda c: c["ts"])
            exp_bid, exp_ask = last_c["yes_bid"], last_c["yes_ask"]
        else:
            exp_bid = exp_ask = None
        assert (r["yes_bid"], r["yes_ask"]) == (exp_bid, exp_ask), (r["market_ticker"], r["ts"], r, exp_bid, exp_ask)
        exp_vol = sum((c["volume"] for c in cs if r["ts"] - STEP < c["ts"] <= r["ts"]), Decimal(0))
        assert r["volume"] == exp_vol, (r["market_ticker"], r["ts"], r["volume"], exp_vol)
        if exp_bid is not None:
            n_quoted += 1
    assert n_quoted > 0, "no quoted rows at all"


def test_4b_quotes_step_other(conn):
    rows = market.quotes(conn, NY, 60)
    assert all(r["ts"].minute == 0 for r in rows), "60-minute grid not aligned"
    assert len({r["market_ticker"] for r in rows}) == 6


def test_10_volume_conserved(conn):
    for event in (NY, CHI):
        rows = market.quotes(conn, event, 15)
        with conn.cursor(row_factory=dict_row) as cur:
            cur.execute(
                "SELECT c.market_ticker, sum(c.volume) AS vol, min(c.ts) AS t0, max(c.ts) AS t1 "
                "FROM candle c JOIN market m ON m.ticker = c.market_ticker "
                "WHERE m.event_ticker = %s GROUP BY c.market_ticker", (event,))
            raw = {r["market_ticker"]: r for r in cur.fetchall()}
        assert len(raw) == 6, (event, len(raw))
        t0 = min(r["t0"] for r in raw.values())
        t1 = max(r["t1"] for r in raw.values())
        first = dt.datetime.fromtimestamp(ceil_to_grid(t0, 900), UTC)
        last = dt.datetime.fromtimestamp(ceil_to_grid(t1, 900), UTC)
        for ticker, r in raw.items():
            mine = [x for x in rows if x["market_ticker"] == ticker]
            assert sum(x["volume"] for x in mine) == r["vol"], (ticker, sum(x["volume"] for x in mine), r["vol"])
            assert mine[0]["ts"] == first and mine[-1]["ts"] == last, (ticker, mine[0]["ts"], mine[-1]["ts"], first, last)


def test_11_edge_fixture(_conn):
    admin = connect()
    admin.autocommit = True
    admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
    admin.execute(f"CREATE DATABASE {TEST_DB}")
    try:
        with connect(TEST_DB) as conn:
            conn.execute(SCHEMA.read_text())
            conn.execute("INSERT INTO underlying (underlying_id, description) VALUES ('U', 'test')")
            conn.execute("INSERT INTO series (ticker, underlying_id) VALUES ('S', 'U')")
            conn.execute("INSERT INTO event (ticker, series_ticker, underlying_id, event_date, settlement_source) "
                         "VALUES ('S-E', 'S', 'U', '2026-01-01', 'test')")
            conn.execute("INSERT INTO market (ticker, event_ticker, open_time, close_time) "
                         "VALUES ('S-E-M', 'S-E', %s, %s)", (utc(2026, 1, 1, 0), utc(2026, 1, 2, 0)))
            for hh, mm, vol in ((12, 0, 5), (12, 10, 3), (12, 40, 2)):
                conn.execute("INSERT INTO candle (market_ticker, ts, yes_bid, yes_ask, volume) "
                             "VALUES ('S-E-M', %s, 0.40, 0.50, %s)",
                             (dt.datetime(2026, 1, 1, hh, mm, tzinfo=UTC), vol))
            rows = market.quotes(conn, "S-E", 15)
            got = [(r["ts"], r["volume"]) for r in rows]
            want = [(dt.datetime(2026, 1, 1, 12, m, tzinfo=UTC), Decimal(v))
                    for m, v in ((0, 5), (15, 3), (30, 0), (45, 2))]
            assert got == want, (got, want)
    finally:
        admin.execute(f"DROP DATABASE IF EXISTS {TEST_DB}")
        admin.close()


def test_5_observations(conn):
    rows = weather.observations(conn, NY)
    w0, w1 = utc(2026, 11, 22, 5), utc(2026, 11, 23, 5)
    assert all(w0 <= r["observed_at"] < w1 for r in rows), "observation outside window"
    assert len(rows) >= 24, len(rows)
    off = [r for r in weather.official(conn, NY) if r["source"] == "kalshi"][0]["high_f"]
    top = max(r["temp_f"] for r in rows)
    assert abs(top - off) <= 2, (top, off)
    times = [r["observed_at"] for r in rows]
    assert times == sorted(times)


def test_6_forecasts(conn):
    rows = weather.forecasts(conn, NY)
    w0, w1 = utc(2026, 11, 22, 5), utc(2026, 11, 23, 5)
    assert len({r["model"] for r in rows}) > 1, "one model only"
    assert len({r["lead_day"] for r in rows}) > 1, "one lead only"
    assert all(w0 <= r["valid_time"] < w1 for r in rows), "valid_time outside window"
    assert all(r["temp_f"] is not None for r in rows), "NULL temp_f"
    one = weather.forecasts(conn, NY, lead_day=1)
    assert one and {r["lead_day"] for r in one} == {1}, {r["lead_day"] for r in one}
    assert all(w0 <= r["valid_time"] < w1 for r in one)
    assert all(r["temp_f"] is not None for r in one)
    assert len(one) < len(rows)


def test_7_official(conn):
    rows = weather.official(conn, NY)
    assert len(rows) == 2, len(rows)
    assert [r["source"] for r in rows] == ["kalshi", "nws_cli"], [r["source"] for r in rows]
    assert all(r["high_f"] == 61 for r in rows), [r["high_f"] for r in rows]


def test_8_chicago_window(conn):
    w0, w1 = utc(2026, 11, 22, 6), utc(2026, 11, 23, 6)
    obs = weather.observations(conn, CHI)
    assert obs, "no Chicago observations"
    assert all(w0 <= r["observed_at"] < w1 for r in obs), "observation outside Chicago window"
    # the 06:00 UTC edge, not 05:00 (NY window): nothing before 06:00 may appear
    assert min(r["observed_at"] for r in obs) >= w0
    fc = weather.forecasts(conn, CHI)
    assert fc, "no Chicago forecasts"
    assert all(w0 <= r["valid_time"] < w1 for r in fc), "forecast outside Chicago window"
    # both edges: raw rows exactly at 05:xx UTC exist but must be excluded; 06:00 included if present
    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM observation_hourly WHERE station_id = 'KMDW' "
            "AND observed_at >= %s AND observed_at < %s", (w0, w1))
        expected = cur.fetchone()[0]
    assert len(obs) == expected, (len(obs), expected)


def count_trading(conn):
    counts = {}
    with conn.cursor() as cur:
        for t in TRADING:
            cur.execute(f'SELECT count(*) FROM "{t}"')
            counts[t] = cur.fetchone()[0]
    return counts


def test_9_read_only(conn):
    TS = psycopg.pq.TransactionStatus
    before = count_trading(conn)
    conn.rollback()
    calls = [
        lambda: market.cities(conn),
        lambda: market.event_days(conn, "NYC_HIGH"),
        lambda: market.ladder(conn, NY),
        lambda: market.quotes(conn, NY, 15),
        lambda: weather.observations(conn, NY),
        lambda: weather.forecasts(conn, NY),
        lambda: weather.forecasts(conn, NY, lead_day=1),
        lambda: weather.official(conn, NY),
    ]
    for call in calls:
        # a read-only transaction makes any write raise an error
        with conn.transaction(force_rollback=True):
            conn.execute("SET TRANSACTION READ ONLY")
            call()
            assert conn.info.transaction_status == TS.INTRANS
        assert conn.info.transaction_status == TS.IDLE
    # also without a read-only guard, the call leaves nothing behind
    for call in calls:
        call()
        conn.rollback()
    after = count_trading(conn)
    conn.rollback()
    assert before == after, (before, after)


TESTS = [
    ("1 cities", test_1_cities),
    ("2 event_days", test_2_event_days),
    ("3 ladder", test_3_ladder),
    ("4 quotes vs raw candles", test_4_quotes),
    ("4b quotes 60-minute grid (extra)", test_4b_quotes_step_other),
    ("10 volume conserved per market", test_10_volume_conserved),
    ("11 edge fixture in throwaway db", test_11_edge_fixture),
    ("5 observations", test_5_observations),
    ("6 forecasts", test_6_forecasts),
    ("7 official", test_7_official),
    ("8 chicago window", test_8_chicago_window),
    ("9 never writes", test_9_read_only),
]


def main():
    with connect() as conn:
        for name, fn in TESTS:
            try:
                fn(conn)
                report(name, True)
            except Exception as exc:
                conn.rollback()
                report(name, False, f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
    print(f"{len(TESTS) - len(failures)}/{len(TESTS)} passed")
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
