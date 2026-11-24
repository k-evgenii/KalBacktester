"""Matching engine: record what happened to each order intent of one run.

Run from the repo root:
    uv run python -m engine.match_intents <intents.csv> --strategy NAME [--db NAME]
Rules are in .claude/specs/012-matching-engine.md. Everything is written in one transaction.
"""
import argparse
import csv
import subprocess
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from math import floor
from pathlib import Path

import polars as pl
from psycopg.types.json import Jsonb

from db.connect import connect

REPO_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_PARAMS = {
    "fill_delay_minutes": 1,
    "volume_window_minutes": 60,
    "volume_share": "0.10",
    "fee_rate": "0.07",
    "starting_cash": "1000",
}


def code_version():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=REPO_ROOT, capture_output=True, text=True, check=True,
        )
        return result.stdout.strip() or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def check_intents(intents, markets):
    """Raise ValueError on the first invalid intent."""
    if not intents:
        raise ValueError("no intents")
    for intent in intents:
        if intent["market_ticker"] not in markets:
            raise ValueError(f"unknown market {intent['market_ticker']}")
        if intent["side"] not in ("yes", "no"):
            raise ValueError(f"invalid side {intent['side']!r}")
        if intent["action"] not in ("buy", "sell"):
            raise ValueError(f"invalid action {intent['action']!r}")
        quantity = intent["quantity"]
        if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
            raise ValueError(f"invalid quantity {quantity!r}")


def lock_time(cur, event_ticker):
    """UTC top of the hour of the day's high (NWS CLI), or None if unknown."""
    cur.execute(
        """
        SELECT e.event_date, s.std_utc_offset_h, o.high_time_lst
        FROM event e
        JOIN underlying u ON u.underlying_id = e.underlying_id
        JOIN station s ON s.station_id = u.station_id
        JOIN official_daily o ON o.station_id = s.station_id
                             AND o.climate_date = e.event_date AND o.source = 'nws_cli'
        WHERE e.ticker = %s
        """,
        (event_ticker,),
    )
    row = cur.fetchone()
    if row is None or row[2] is None:
        return None
    event_date, offset_h, high_time_lst = row
    try:
        digits, ampm = high_time_lst.split()
        t = datetime.strptime(digits.zfill(4) + ampm, "%I%M%p").time()
    except ValueError:
        return None
    local = datetime.combine(event_date, t)  # naive, local standard time
    utc = (local - timedelta(hours=offset_h)).replace(tzinfo=timezone.utc)
    return utc.replace(minute=0, second=0, microsecond=0)


def fee_for(fee_rate, fill, price):
    raw = Decimal(fee_rate) * fill * price * (1 - price)
    return raw.quantize(Decimal("0.01"), rounding=ROUND_CEILING)


def prepare_lookups(conn, rows, window):
    """Add the quote and the window volume to every row (Polars, Decimal kept exact).

    rows are already in processing order; each gets a position "idx".
    """
    intents = pl.DataFrame(
        {
            "idx": list(range(len(rows))),
            "market_ticker": [r["market_ticker"] for r in rows],
            "decided_at": [r["decided_at"] for r in rows],
            "fill_time": [r["fill_time"] for r in rows],
        }
    )
    tickers = sorted({r["market_ticker"] for r in rows})
    candles = pl.read_database(
        "SELECT market_ticker, ts, yes_bid, yes_ask, volume FROM candle "
        "WHERE market_ticker = ANY(%s) ORDER BY ts",
        conn,
        execute_options={"params": (tickers,)},
        schema_overrides={"market_ticker": pl.String, "ts": pl.Datetime("us", "UTC")},
        infer_schema_length=None,
    )

    # quote in force at fill_time: last candle with ts <= fill_time
    quotes = intents.sort("fill_time").join_asof(
        candles.select("market_ticker", "ts", "yes_bid", "yes_ask"),
        left_on="fill_time", right_on="ts", by="market_ticker", strategy="backward",
        check_sortedness=False,
    )

    # volume traded in (decided_at - window, decided_at]
    volumes = (
        intents.join(candles.select("market_ticker", "ts", "volume"), on="market_ticker")
        .filter(
            (pl.col("ts") > pl.col("decided_at") - window)
            & (pl.col("ts") <= pl.col("decided_at"))
        )
        .group_by("idx")
        .agg(pl.col("volume").sum().alias("window_volume"))
    )

    prepared = quotes.join(volumes, on="idx", how="left").sort("idx")
    for row, extra in zip(rows, prepared.iter_rows(named=True)):
        row["yes_bid"] = extra["yes_bid"]
        row["yes_ask"] = extra["yes_ask"]
        row["window_volume"] = extra["window_volume"] or Decimal(0)


def decide(row, net, market, lock, volume_share, fee_rate):
    """Apply rules 1-7 to one prepared row.

    Returns a list of outcome tuples (outcome, quantity, price, fee, reason_code).
    """
    side, action, quantity = row["side"], row["action"], row["quantity"]
    fill_time = row["fill_time"]

    # rule 1
    if not (market["open_time"] < fill_time <= market["close_time"]):
        return [("rejected", 0, None, None, "market_not_open")]
    # rule 2
    if lock is None:
        return [("blocked", 0, None, None, "high_time_unknown")]
    if fill_time >= lock:
        return [("blocked", 0, None, None, "after_daily_high")]
    # rule 3
    if action == "buy" and side == "yes" and net < 0:
        return [("blocked", 0, None, None, "opposite_position")]
    if action == "buy" and side == "no" and net > 0:
        return [("blocked", 0, None, None, "opposite_position")]
    # rule 4
    held = net if side == "yes" else -net
    if action == "sell" and held <= 0:
        return [("rejected", 0, None, None, "nothing_to_sell")]
    # rule 5
    yes_bid, yes_ask = row["yes_bid"], row["yes_ask"]
    if side == "yes" and action == "buy":
        price = yes_ask
    elif side == "yes" and action == "sell":
        price = yes_bid
    elif side == "no" and action == "buy":
        price = None if yes_bid is None else 1 - yes_bid
    else:  # sell no
        price = None if yes_ask is None else 1 - yes_ask
    if price is None:
        return [("rejected", 0, None, None, "no_quote")]
    # rule 6
    available = floor(volume_share * row["window_volume"])
    fill = min(quantity, available)
    remainder_reason = "volume_cap"
    if action == "sell" and held < fill:
        fill = held
        remainder_reason = "nothing_to_sell"  # the holding, not the volume, was the limit
    if fill == 0:
        return [("rejected", 0, None, None, "volume_cap")]
    # rule 7
    outcomes = [("filled", fill, price, fee_for(fee_rate, Decimal(fill), price), None)]
    if fill < quantity:
        outcomes.append(("rejected", 0, None, None, remainder_reason))
    return outcomes


def signed_units(side, action, fill):
    """Change of the net YES position for a fill."""
    if (side, action) in (("yes", "buy"), ("no", "sell")):
        return fill
    return -fill


def position_states(fills):
    """For one market's fills (in order): list of (occurred_at, net, avg_cost) after each fill.

    avg_cost is the quantity-weighted average price of the fills that built the position
    (price of the side held); it resets when the position returns to 0.
    """
    states = []
    net = 0
    avg_cost = None
    for occurred_at, side, action, fill, price in fills:
        old_held = abs(net)
        net += signed_units(side, action, fill)
        if action == "buy":
            avg_cost = (avg_cost * old_held + price * fill) / (old_held + fill) if old_held else price
        if net == 0:
            avg_cost = None
        states.append((occurred_at, net, avg_cost))
    return states


def snapshot_rows(fills_by_market, markets):
    """Rows (as_of_date, ticker, net_units, avg_cost) for each UTC day end with an open position."""
    states = {t: position_states(f) for t, f in fills_by_market.items()}
    if not states:
        return []
    first_day = min(f[0][0] for f in fills_by_market.values()).date()
    # last day on which a position can still be open
    last_day = first_day
    for ticker, market_states in states.items():
        last_fill_day = market_states[-1][0].date()
        last_day = max(last_day, last_fill_day)
        if market_states[-1][1] != 0:
            if markets[ticker]["settled_at"] is not None:
                last_day = max(last_day, markets[ticker]["settled_at"].date() - timedelta(days=1))
            else:
                last_day = max(last_day, markets[ticker]["close_time"].date())

    rows = []
    day = first_day
    while day <= last_day:
        next_midnight = datetime.combine(day + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc)
        for ticker, market_states in states.items():
            settled_at = markets[ticker]["settled_at"]
            if settled_at is not None and settled_at < next_midnight:
                continue
            before = [s for s in market_states if s[0] < next_midnight]
            if before and before[-1][1] != 0:
                rows.append((day, ticker, before[-1][1], before[-1][2]))
        day += timedelta(days=1)
    return rows


def load_markets(cur, tickers):
    cur.execute(
        "SELECT ticker, event_ticker, open_time, close_time, result, settled_at "
        "FROM market WHERE ticker = ANY(%s)",
        (tickers,),
    )
    return {
        r[0]: {
            "event_ticker": r[1], "open_time": r[2], "close_time": r[3],
            "result": r[4], "settled_at": r[5],
        }
        for r in cur.fetchall()
    }


def write_run(conn, strategy, params, intents):
    cur = conn.cursor()
    used = {**DEFAULT_PARAMS, **params}
    used = {k: (v if isinstance(v, int) else str(v)) for k, v in used.items()}
    delay = timedelta(minutes=used["fill_delay_minutes"])
    window = timedelta(minutes=used["volume_window_minutes"])
    volume_share = Decimal(used["volume_share"])
    fee_rate = used["fee_rate"]

    markets = load_markets(cur, sorted({i["market_ticker"] for i in intents}))
    check_intents(intents, markets)

    # processing order: decided_at, ties in input order (sorted() is stable)
    rows = [dict(i, fill_time=i["decided_at"] + delay) for i in sorted(intents, key=lambda i: i["decided_at"])]
    prepare_lookups(conn, rows, window)
    locks = {}
    for ticker, market in markets.items():
        if market["event_ticker"] not in locks:
            locks[market["event_ticker"]] = lock_time(cur, market["event_ticker"])

    cur.execute(
        "INSERT INTO run (strategy, params, code_version, starting_cash, date_start, date_end, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'running') RETURNING run_id",
        (
            strategy, Jsonb(used), code_version(), Decimal(used["starting_cash"]),
            rows[0]["decided_at"].astimezone(timezone.utc).date(),
            rows[-1]["decided_at"].astimezone(timezone.utc).date(),
        ),
    )
    run_id = cur.fetchone()[0]

    net = {}
    fills_by_market = {}
    for row in rows:
        ticker = row["market_ticker"]
        market = markets[ticker]
        cur.execute(
            "INSERT INTO order_intent (run_id, decided_at, market_ticker, side, action, quantity, reason) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING intent_id",
            (run_id, row["decided_at"], ticker, row["side"], row["action"], row["quantity"], row["reason"]),
        )
        intent_id = cur.fetchone()[0]

        outcomes = decide(
            row, net.get(ticker, 0), market, locks[market["event_ticker"]], volume_share, fee_rate
        )
        for outcome, quantity, price, fee, reason_code in outcomes:
            cur.execute(
                "INSERT INTO order_outcome (intent_id, occurred_at, outcome, quantity, price, fee, reason_code) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s)",
                (intent_id, row["fill_time"], outcome, quantity, price, fee, reason_code),
            )
            if outcome == "filled":
                net[ticker] = net.get(ticker, 0) + signed_units(row["side"], row["action"], quantity)
                fills_by_market.setdefault(ticker, []).append(
                    (row["fill_time"], row["side"], row["action"], quantity, price)
                )

    # settlements of positions still held
    for ticker, units in net.items():
        result = markets[ticker]["result"]
        if units == 0 or result is None:
            continue
        if units > 0 and result == "yes":
            payout = units
        elif units < 0 and result == "no":
            payout = -units
        else:
            payout = 0
        cur.execute(
            "INSERT INTO settlement (run_id, market_ticker, settled_at, result, net_units, payout) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            (run_id, ticker, markets[ticker]["settled_at"], result, units, payout),
        )

    for as_of_date, ticker, units, avg_cost in snapshot_rows(fills_by_market, markets):
        cur.execute(
            "INSERT INTO position_snapshot (run_id, as_of_date, market_ticker, net_units, avg_cost) "
            "VALUES (%s, %s, %s, %s, %s)",
            (run_id, as_of_date, ticker, units, avg_cost),
        )

    cur.execute("UPDATE run SET status = 'done', finished_at = now() WHERE run_id = %s", (run_id,))
    return run_id


def match_intents(conn, strategy, params, intents):
    """Record the outcome of every intent of one run; returns the run_id."""
    try:
        run_id = write_run(conn, strategy, params, intents)
        conn.commit()
        return run_id
    except Exception:
        conn.rollback()
        raise


def read_csv(path):
    intents = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            intents.append(
                {
                    "decided_at": datetime.fromisoformat(row["decided_at"]),
                    "market_ticker": row["market_ticker"],
                    "side": row["side"],
                    "action": row["action"],
                    "quantity": int(row["quantity"]),
                    "reason": row["reason"] or None,
                }
            )
    return intents


def main():
    parser = argparse.ArgumentParser(description="Match order intents against stored candles.")
    parser.add_argument("intents_csv")
    parser.add_argument("--strategy", required=True)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()

    intents = read_csv(args.intents_csv)
    with connect(args.db) as conn:
        run_id = match_intents(conn, args.strategy, {}, intents)
        with conn.cursor() as cur:
            cur.execute(
                "SELECT o.outcome, o.reason_code, count(*) FROM order_outcome o "
                "JOIN order_intent i ON i.intent_id = o.intent_id WHERE i.run_id = %s "
                "GROUP BY 1, 2 ORDER BY 1, 2",
                (run_id,),
            )
            counts = cur.fetchall()
    print(f"run_id {run_id}")
    for outcome, reason_code, count in counts:
        print(f"{outcome:9} {reason_code or '-':20} {count}")


if __name__ == "__main__":
    main()
