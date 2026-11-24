"""Reconciliation of one run (spec 013).

Run from the repo root: uv run python -m recon.check_run <run_id> [--db NAME]
Re-checks intents, outcomes, settlements and snapshots of a run against the market data
and the rules of spec 012. SELECT only; prints one line per check; exits 0 on PASS, 1 on FAIL.
Deliberately independent of engine/: plain SQL reads, bisect and Decimal, no Polars.
"""
import argparse
import sys
from bisect import bisect_right
from datetime import datetime, time, timedelta, timezone
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal

from db.connect import connect

ONE = Decimal(1)
CENT = Decimal("0.01")
AVG_COST_TOLERANCE = Decimal("0.000001")


# ---------- reading the database ----------

def load_run(conn, run_id):
    """Everything the checks need, as plain Python lists and dicts."""
    cur = conn.cursor()
    cur.execute("SELECT params FROM run WHERE run_id = %s", (run_id,))
    row = cur.fetchone()
    if row is None:
        raise ValueError(f"unknown run_id {run_id}")
    params = row[0]
    data = {
        "delay": timedelta(minutes=int(params["fill_delay_minutes"])),
        "window": timedelta(minutes=int(params["volume_window_minutes"])),
        "volume_share": Decimal(str(params["volume_share"])),
        "fee_rate": Decimal(str(params["fee_rate"])),
    }

    cur.execute(
        "SELECT intent_id, decided_at, market_ticker, side, action, quantity "
        "FROM order_intent WHERE run_id = %s ORDER BY intent_id", (run_id,))
    data["intents"] = [
        {"id": r[0], "decided_at": r[1], "market": r[2], "side": r[3], "action": r[4], "quantity": r[5]}
        for r in cur.fetchall()]

    cur.execute(
        "SELECT o.intent_id, o.occurred_at, o.outcome, o.quantity, o.price, o.fee, o.reason_code "
        "FROM order_outcome o JOIN order_intent i ON i.intent_id = o.intent_id "
        "WHERE i.run_id = %s ORDER BY o.outcome_id", (run_id,))
    data["outcomes"] = {}
    for r in cur.fetchall():
        outcome = {"intent_id": r[0], "occurred_at": r[1], "outcome": r[2], "quantity": r[3],
                   "price": r[4], "fee": r[5], "reason_code": r[6]}
        data["outcomes"].setdefault(r[0], []).append(outcome)

    tickers = sorted({i["market"] for i in data["intents"]})
    cur.execute(
        "SELECT m.ticker, m.open_time, m.close_time, m.result, m.settled_at, m.event_ticker, "
        "       e.event_date, s.std_utc_offset_h, d.high_time_lst "
        "FROM market m "
        "JOIN event e ON e.ticker = m.event_ticker "
        "JOIN underlying u ON u.underlying_id = e.underlying_id "
        "LEFT JOIN station s ON s.station_id = u.station_id "
        "LEFT JOIN official_daily d ON d.station_id = u.station_id "
        "     AND d.climate_date = e.event_date AND d.source = 'nws_cli' "
        "WHERE m.ticker = ANY(%s)", (tickers,))
    data["markets"] = {}
    for r in cur.fetchall():
        data["markets"][r[0]] = {
            "open": r[1], "close": r[2], "result": r[3], "settled_at": r[4], "event": r[5],
            "event_date": r[6], "lock": lock_time(r[6], r[7], r[8])}

    cur.execute(
        "SELECT market_ticker, ts, yes_bid, yes_ask, volume FROM candle "
        "WHERE market_ticker = ANY(%s) ORDER BY market_ticker, ts", (tickers,))
    data["candles"] = {t: {"ts": [], "bid": [], "ask": [], "volume": []} for t in tickers}
    for ticker, ts, bid, ask, volume in cur.fetchall():
        series = data["candles"][ticker]
        series["ts"].append(ts)
        series["bid"].append(bid)
        series["ask"].append(ask)
        series["volume"].append(volume)

    cur.execute(
        "SELECT as_of_date, market_ticker, net_units, avg_cost FROM position_snapshot "
        "WHERE run_id = %s", (run_id,))
    data["snapshots"] = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}

    cur.execute(
        "SELECT market_ticker, settled_at, result, net_units, payout FROM settlement "
        "WHERE run_id = %s", (run_id,))
    data["settlements"] = {r[0]: (r[1], r[2], r[3], r[4]) for r in cur.fetchall()}

    cur.execute(
        "SELECT market_ticker, result, expected_result FROM event_outcome "
        "WHERE market_ticker = ANY(%s)", (tickers,))
    data["event_outcomes"] = cur.fetchall()
    return data


# ---------- definitions from the spec ----------

def lock_time(event_date, offset_hours, high_time_text):
    """UTC lock time from the NWS 'high time' text such as '225 PM'; None if unknown."""
    if offset_hours is None or high_time_text is None:
        return None
    parts = high_time_text.split(" ")
    if len(parts) != 2:
        return None
    try:
        clock = datetime.strptime(parts[0].zfill(4) + " " + parts[1], "%I%M %p")
    except ValueError:
        return None
    local = datetime.combine(event_date, time(clock.hour, clock.minute), tzinfo=timezone.utc)
    utc = local - timedelta(hours=offset_hours)
    return utc.replace(minute=0, second=0, microsecond=0)


def fill_time(data, intent):
    return intent["decided_at"] + data["delay"]


def quote_at(data, market, t):
    """(yes_bid, yes_ask) of the last candle at or before t, or None."""
    series = data["candles"][market]
    pos = bisect_right(series["ts"], t)
    if pos == 0:
        return None
    return series["bid"][pos - 1], series["ask"][pos - 1]


def needed_price(data, intent, t):
    """Price of the side traded from the quote at t; None when no candle or side missing."""
    quote = quote_at(data, intent["market"], t)
    if quote is None:
        return None
    bid, ask = quote
    if intent["side"] == "yes":
        return ask if intent["action"] == "buy" else bid
    if intent["action"] == "buy":
        return None if bid is None else ONE - bid
    return None if ask is None else ONE - ask


def available(data, intent):
    series = data["candles"][intent["market"]]
    upper = bisect_right(series["ts"], intent["decided_at"])
    lower = bisect_right(series["ts"], intent["decided_at"] - data["window"])
    total = sum(series["volume"][lower:upper], Decimal(0))
    return (data["volume_share"] * total).to_integral_value(rounding=ROUND_FLOOR)


def fee(data, quantity, price):
    return (data["fee_rate"] * quantity * price * (ONE - price)).quantize(CENT, rounding=ROUND_CEILING)


def is_open(market, t):
    return market["open"] < t <= market["close"]


def signed_units(intent, quantity):
    """Change in YES units caused by a fill."""
    if intent["side"] == "yes":
        return quantity if intent["action"] == "buy" else -quantity
    return -quantity if intent["action"] == "buy" else quantity


def held_side(intent, net):
    return net if intent["side"] == "yes" else -net


def replay(data):
    """Walk the intents in intent_id order. Returns (held before each intent, fills in order)."""
    net = {}
    held_before = {}
    fills = []
    for intent in data["intents"]:
        current = net.get(intent["market"], Decimal(0))
        held_before[intent["id"]] = held_side(intent, current)
        for outcome in data["outcomes"].get(intent["id"], []):
            if outcome["outcome"] == "filled":
                net[intent["market"]] = current + signed_units(intent, outcome["quantity"])
                fills.append((intent, outcome))
    return held_before, fills


def positions_at(data, fills, cutoff):
    """{market: (net_units, avg_cost)} using fills before cutoff; settled markets are flat."""
    state = {}
    ordered = sorted(fills, key=lambda f: (f[1]["occurred_at"], f[0]["id"]))
    for intent, outcome in ordered:
        if outcome["occurred_at"] >= cutoff:
            continue
        net, avg = state.get(intent["market"], (Decimal(0), Decimal(0)))
        change = signed_units(intent, outcome["quantity"])
        if abs(net + change) > abs(net):
            avg = (avg * abs(net) + outcome["quantity"] * outcome["price"]) / (abs(net) + outcome["quantity"])
        net = net + change
        if net == 0:
            avg = Decimal(0)
        state[intent["market"]] = (net, avg)
    result = {}
    for market, (net, avg) in state.items():
        settled_at = data["markets"][market]["settled_at"]
        if settled_at is not None and settled_at < cutoff:
            continue
        if net != 0:
            result[market] = (net, avg)
    return result


def utc_date(t):
    return t.astimezone(timezone.utc).date()


def expected_snapshots(data, fills):
    """{(date, market): (net_units, avg_cost)} the replay requires."""
    if not fills:
        return {}
    traded = {intent["market"] for intent, _ in fills}
    last_open_day = {}
    for market in traded:
        info = data["markets"][market]
        if info["result"] is not None:
            last_open_day[market] = utc_date(info["settled_at"]) - timedelta(days=1)
        else:
            last_open_day[market] = utc_date(info["close"])
    first_day = min(utc_date(outcome["occurred_at"]) for _, outcome in fills)
    last_day = max(last_open_day.values())
    expected = {}
    day = first_day
    while day <= last_day:
        cutoff = datetime.combine(day + timedelta(days=1), time(0), tzinfo=timezone.utc)
        for market, position in positions_at(data, fills, cutoff).items():
            if day <= last_open_day[market]:
                expected[(day, market)] = position
        day += timedelta(days=1)
    return expected


# ---------- checks ----------

def make_check(check_id, name, problems, ok_detail="ok"):
    if problems:
        return {"id": check_id, "name": name, "status": "fail",
                "detail": f"{len(problems)} problem(s)", "examples": problems[:10]}
    return {"id": check_id, "name": name, "status": "pass", "detail": ok_detail, "examples": []}


def filled_rows(data):
    """(intent, outcome) for every filled outcome row, in intent order."""
    rows = []
    for intent in data["intents"]:
        for outcome in data["outcomes"].get(intent["id"], []):
            if outcome["outcome"] == "filled":
                rows.append((intent, outcome))
    return rows


def check_1_outcome_rows(data):
    problems = []
    for intent in data["intents"]:
        rows = data["outcomes"].get(intent["id"], [])
        kinds = sorted(r["outcome"] for r in rows)
        ok = len(rows) == 1 or kinds == ["filled", "rejected"]
        if not ok:
            problems.append(f"intent {intent['id']} {intent['market']}: expected 1 row or filled+rejected, got {kinds}")
    return make_check(1, "every intent has a valid set of outcome rows", problems)


def check_2_timing(data):
    problems = []
    for intent in data["intents"]:
        expected = fill_time(data, intent)
        for outcome in data["outcomes"].get(intent["id"], []):
            if outcome["occurred_at"] != expected:
                problems.append(f"intent {intent['id']} {intent['market']}: occurred_at {outcome['occurred_at']}, expected {expected}")
    return make_check(2, "outcome time is decided_at plus the fill delay", problems)


def check_3_quantity(data):
    problems = []
    for intent, outcome in filled_rows(data):
        if not (0 < outcome["quantity"] <= intent["quantity"]):
            problems.append(f"intent {intent['id']} {intent['market']}: filled {outcome['quantity']}, intent quantity {intent['quantity']}")
    return make_check(3, "filled quantity is positive and within the intent", problems)


def check_4_price(data):
    problems = []
    for intent, outcome in filled_rows(data):
        expected = needed_price(data, intent, outcome["occurred_at"])
        if expected is None or outcome["price"] != expected:
            problems.append(f"intent {intent['id']} {intent['market']}: fill price {outcome['price']}, quote price {expected}")
    return make_check(4, "fill price is the quote price of the needed side", problems)


def check_5_market_hours(data):
    problems = []
    for intent, outcome in filled_rows(data):
        market = data["markets"][intent["market"]]
        if not is_open(market, outcome["occurred_at"]):
            problems.append(f"intent {intent['id']} {intent['market']}: fill at {outcome['occurred_at']}, open {market['open']} close {market['close']}")
    return make_check(5, "fills are inside market hours", problems)


def check_6_lock(data):
    problems = []
    for intent, outcome in filled_rows(data):
        lock = data["markets"][intent["market"]]["lock"]
        if lock is None or outcome["occurred_at"] >= lock:
            problems.append(f"intent {intent['id']} {intent['market']}: fill at {outcome['occurred_at']}, lock {lock}")
    return make_check(6, "fills are before the daily-high lock", problems)


def check_7_volume(data):
    problems = []
    for intent, outcome in filled_rows(data):
        cap = available(data, intent)
        if outcome["quantity"] > cap:
            problems.append(f"intent {intent['id']} {intent['market']}: filled {outcome['quantity']}, available {cap}")
    return make_check(7, "fills respect the volume cap", problems)


def check_8_fee(data):
    problems = []
    for intent, outcome in filled_rows(data):
        expected = fee(data, outcome["quantity"], outcome["price"])
        if outcome["fee"] != expected:
            problems.append(f"intent {intent['id']} {intent['market']}: fee {outcome['fee']}, expected {expected}")
    return make_check(8, "fees are rounded up to the cent", problems)


def check_9_replay(data):
    problems = []
    net = {}
    for intent in data["intents"]:
        current = net.get(intent["market"], Decimal(0))
        for outcome in data["outcomes"].get(intent["id"], []):
            if outcome["outcome"] != "filled":
                continue
            held = held_side(intent, current)
            other_held = -held
            if intent["action"] == "sell" and outcome["quantity"] > held:
                problems.append(f"intent {intent['id']} {intent['market']}: sold {outcome['quantity']}, held {held}")
            if intent["action"] == "buy" and other_held > 0:
                problems.append(f"intent {intent['id']} {intent['market']}: bought {intent['side']} while holding {other_held} of the other side")
            net[intent["market"]] = current + signed_units(intent, outcome["quantity"])
    return make_check(9, "replay: no naked shorts or opposite positions", problems)


def non_fill_problem(data, intent, outcome, rows, held):
    """Text describing why a non-fill is not justified, or None when it is justified."""
    market = data["markets"][intent["market"]]
    t = fill_time(data, intent)
    code = outcome["reason_code"]
    fill_rows = [r for r in rows if r["outcome"] == "filled"]
    filled = fill_rows[0]["quantity"] if fill_rows else None
    if code == "market_not_open":
        if is_open(market, t):
            return f"market is open at {t}"
    elif code == "high_time_unknown":
        if market["lock"] is not None:
            return f"lock exists: {market['lock']}"
    elif code == "after_daily_high":
        if market["lock"] is None or t < market["lock"]:
            return f"fill time {t}, lock {market['lock']}"
    elif code == "opposite_position":
        other_held = -held
        if intent["action"] != "buy" or other_held <= 0:
            return f"action {intent['action']}, other side held {other_held}"
    elif code == "nothing_to_sell":
        if intent["action"] != "sell":
            return f"action {intent['action']}"
        if filled is None:
            if held > 0:
                return f"held {held}"
        elif not (filled == held and filled < intent["quantity"]):
            return f"fill {filled}, held {held}, quantity {intent['quantity']}"
    elif code == "no_quote":
        price = needed_price(data, intent, t)
        if price is not None:
            return f"quote price {price} exists at {t}"
    elif code == "volume_cap":
        cap = available(data, intent)
        if cap >= intent["quantity"]:
            return f"available {cap}, quantity {intent['quantity']}"
        if filled is not None and filled != cap:
            return f"fill {filled}, available {cap}"
    else:
        return f"unknown reason code {code}"
    return None


def check_10_non_fills(data, held_before):
    problems = []
    for intent in data["intents"]:
        rows = data["outcomes"].get(intent["id"], [])
        for outcome in rows:
            if outcome["outcome"] == "filled":
                continue
            problem = non_fill_problem(data, intent, outcome, rows, held_before[intent["id"]])
            if problem:
                problems.append(f"intent {intent['id']} {intent['market']}: {outcome['reason_code']} not justified: {problem}")
    return make_check(10, "every non-fill is justified by the data", problems)


def check_11_snapshots(data, fills):
    expected = expected_snapshots(data, fills)
    actual = data["snapshots"]
    problems = []
    for key in sorted(set(expected) | set(actual)):
        day, market = key
        if key not in actual:
            problems.append(f"{market} {day}: snapshot missing, expected {expected[key][0]}")
        elif key not in expected:
            problems.append(f"{market} {day}: unexpected snapshot with net_units {actual[key][0]}")
        elif actual[key][0] != expected[key][0]:
            problems.append(f"{market} {day}: net_units {actual[key][0]}, expected {expected[key][0]}")
        elif abs(actual[key][1] - expected[key][1]) > AVG_COST_TOLERANCE:
            problems.append(f"{market} {day}: avg_cost {actual[key][1]}, expected {expected[key][1]}")
    return make_check(11, "position snapshots equal the replay", problems)


def check_12_settlements(data, fills):
    final = {}
    for intent, outcome in fills:
        final[intent["market"]] = final.get(intent["market"], Decimal(0)) + signed_units(intent, outcome["quantity"])
    expected = {}
    for market, net in final.items():
        info = data["markets"][market]
        if net != 0 and info["result"] is not None:
            wins = (net > 0 and info["result"] == "yes") or (net < 0 and info["result"] == "no")
            payout = abs(net) if wins else Decimal(0)
            expected[market] = (info["settled_at"], info["result"], net, payout)
    actual = data["settlements"]
    problems = []
    for market in sorted(set(expected) | set(actual)):
        if market not in actual:
            problems.append(f"{market}: settlement missing, expected {expected[market]}")
        elif market not in expected:
            problems.append(f"{market}: unexpected settlement {actual[market]}")
        elif actual[market] != expected[market]:
            problems.append(f"{market}: settlement {actual[market]}, expected {expected[market]}")
    return make_check(12, "settlements equal what the replay requires", problems)


def check_13_event_outcome(data):
    problems = []
    for market, result, expected_result in data["event_outcomes"]:
        if result is not None and expected_result is not None and result != expected_result:
            problems.append(f"{market}: stored result {result}, expected by bracket {expected_result}")
    return make_check(13, "market results match their brackets", problems)


# ---------- report ----------

def check_run(conn, run_id):
    data = load_run(conn, run_id)
    held_before, fills = replay(data)
    checks = [
        check_1_outcome_rows(data),
        check_2_timing(data),
        check_3_quantity(data),
        check_4_price(data),
        check_5_market_hours(data),
        check_6_lock(data),
        check_7_volume(data),
        check_8_fee(data),
        check_9_replay(data),
        check_10_non_fills(data, held_before),
        check_11_snapshots(data, fills),
        check_12_settlements(data, fills),
        check_13_event_outcome(data),
    ]
    all_outcomes = [o for rows in data["outcomes"].values() for o in rows]
    counts = {
        "intents": len(data["intents"]),
        "filled": sum(1 for o in all_outcomes if o["outcome"] == "filled"),
        "rejected": sum(1 for o in all_outcomes if o["outcome"] == "rejected"),
        "blocked": sum(1 for o in all_outcomes if o["outcome"] == "blocked"),
        "settlements": len(data["settlements"]),
        "snapshots": len(data["snapshots"]),
    }
    failed = any(c["status"] == "fail" for c in checks)
    return {"verdict": "FAIL" if failed else "PASS", "run_id": run_id,
            "checked_at": datetime.now(timezone.utc).isoformat(), "checks": checks, "counts": counts}


def main():
    parser = argparse.ArgumentParser(description="Reconcile one run.")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()
    conn = connect(args.db)
    try:
        report = check_run(conn, args.run_id)
    finally:
        conn.close()
    for check in report["checks"]:
        label = "PASS" if check["status"] == "pass" else "FAIL"
        print(f"[{label}] {check['id']:>2} {check['name']}: {check['detail']}")
        for example in check["examples"]:
            print(f"        {example}")
    print(f"verdict {report['verdict']}  counts {report['counts']}")
    sys.exit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
