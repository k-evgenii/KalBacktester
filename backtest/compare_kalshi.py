"""Engine fills checked against Kalshi's raw data (spec B015).

Run from the repo root: uv run python -m backtest.compare_kalshi <run_id> [--dump DIR ...] [--db NAME]
For every filled outcome of a run: compare the fill price with the raw one-minute bar in the
dump files (A, decides the verdict), look at the public trades in the fill minute (B) and
compare the fee with Kalshi's published formula (C). SELECT only; the dump files are read-only.
Exit 0 if every fill passes the quote check, else 1.
"""
import argparse
import json
import sys
from bisect import bisect_right
from datetime import datetime, timedelta, timezone
from decimal import ROUND_CEILING, Decimal
from pathlib import Path

from db.connect import connect

ONE = Decimal(1)
CENT = Decimal("0.01")
MICRO = Decimal("0.000001")
PRICE_DIGITS = Decimal("0.0001")

MAIN_DUMPS = Path("/home/kondr/KalBacktester/api_probe/kalshi")
DEFAULT_DUMP_DIRS = [
    MAIN_DUMPS / "KXHIGHNY",
    MAIN_DUMPS / "KXHIGHNY_2026-06-01_2026-06-07",
    MAIN_DUMPS / "KXHIGHNY_2026-08-12_2026-08-18",
]
REPORT_DIR = Path(__file__).resolve().parent / "reports"

FILLS_SQL = """
SELECT o.outcome_id, o.intent_id, i.market_ticker, i.side, i.action, o.occurred_at,
       o.quantity, o.price, o.fee, (r.params ->> 'fee_rate')::numeric AS fee_rate
FROM order_outcome o
JOIN order_intent i ON i.intent_id = o.intent_id
JOIN run r          ON r.run_id = i.run_id
WHERE i.run_id = %s AND o.outcome = 'filled'
ORDER BY o.outcome_id;
"""


# ---------- reading the dump ----------

def find_dump_folder(ticker, dump_dirs):
    """The first dump dir that has bars_1min/<ticker>.json, or None."""
    for folder in dump_dirs:
        if (folder / "bars_1min" / f"{ticker}.json").exists():
            return folder
    return None


def read_bars(folder, ticker):
    """Bars sorted by end_period_ts."""
    data = json.loads((folder / "bars_1min" / f"{ticker}.json").read_text())
    return sorted(data["candlesticks"], key=lambda bar: bar["end_period_ts"])


def read_trades(folder, ticker):
    """Trades of the market; an empty list when the folder or the file is missing."""
    if folder is None:
        return []
    path = folder / "trades" / f"{ticker}.json"
    if not path.exists():
        return []
    return json.loads(path.read_text())["trades"]


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


# ---------- A. quote check ----------

def raw_close(bar, side):                    # side "yes_bid" | "yes_ask"
    q = bar.get(side) or {}
    v = q.get("close_dollars", q.get("close"))
    if v is None:
        return None
    v = Decimal(str(v))
    if side == "yes_bid" and v <= 0:
        return None   # 0.00 = no bid
    if side == "yes_ask" and v >= 1:
        return None   # 1.00 = no ask
    return v


def kalshi_price(side, action, bid, ask):
    """Price of the side traded from the raw bid and ask; None when the needed side is missing."""
    if side == "yes":
        needed = ask if action == "buy" else bid
        return needed
    needed = bid if action == "buy" else ask
    return None if needed is None else ONE - needed


def format_price(value):
    return None if value is None else str(value.quantize(PRICE_DIGITS))


def quote_check(folder, fill):
    """Part A: the raw bar in force at the fill time against the engine's fill price."""
    result = {"status": "no_dump", "bar_ts": None, "raw_bid": None, "raw_ask": None, "kalshi_price": None}
    if folder is None:
        return result
    bars = read_bars(folder, fill["market_ticker"])
    stamps = [bar["end_period_ts"] for bar in bars]
    pos = bisect_right(stamps, int(fill["occurred_at"].timestamp()))
    if pos == 0:
        result["status"] = "no_bar"
        return result
    bar = bars[pos - 1]
    bid = raw_close(bar, "yes_bid")
    ask = raw_close(bar, "yes_ask")
    price = kalshi_price(fill["side"], fill["action"], bid, ask)
    result["bar_ts"] = datetime.fromtimestamp(bar["end_period_ts"], tz=timezone.utc).isoformat()
    result["raw_bid"] = format_price(bid)
    result["raw_ask"] = format_price(ask)
    result["kalshi_price"] = format_price(price)
    result["status"] = "match" if price is not None and price == fill["price"] else "mismatch"
    return result


# ---------- B. trades in the fill minute ----------

def trades_check(folder, fill):
    """Part B (informational): public trades with occurred_at - 60 s < created_time <= occurred_at."""
    end = fill["occurred_at"]
    start = end - timedelta(seconds=60)
    price_key = "yes_price_dollars" if fill["side"] == "yes" else "no_price_dollars"
    volume = Decimal(0)
    prices = []
    for trade in read_trades(folder, fill["market_ticker"]):
        created = parse_time(trade["created_time"])
        if start < created <= end:
            volume += Decimal(str(trade["count_fp"]))
            prices.append(Decimal(str(trade[price_key])))
    if not prices:
        return {"n": 0, "volume": "0.00", "min_price": None, "max_price": None, "position": "no_trades"}
    low = min(prices)
    high = max(prices)
    inside = low <= fill["price"] <= high
    return {"n": len(prices), "volume": str(volume), "min_price": format_price(low),
            "max_price": format_price(high), "position": "inside" if inside else "outside"}


# ---------- C. fee ----------

def sql_kalshi_fee(conn, fill):
    """kalshi_fee(q, p, rate) from the database if the function exists, else None."""
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'kalshi_fee'")
    if cur.fetchone() is None:
        return None
    try:
        cur.execute("SELECT kalshi_fee(%s, %s, %s)", (fill["quantity"], fill["price"], fill["fee_rate"]))
        return str(cur.fetchone()[0])
    except Exception as error:
        conn.rollback()
        return f"error: {error}"


def fee_check(conn, fill):
    """Part C (informational): the engine fee against Kalshi's published formula."""
    q = fill["quantity"]
    p = fill["price"]
    rate = fill["fee_rate"]
    raw_fee = rate * q * p * (ONE - p)
    formula_cents = (raw_fee * 100).to_integral_value(rounding=ROUND_CEILING) / 100
    model_fee = raw_fee.quantize(MICRO, rounding=ROUND_CEILING)
    return {"engine": str(fill["fee"].quantize(MICRO)),
            "formula_cents": str(formula_cents.quantize(MICRO)),
            "model_fee": str(model_fee),
            "difference_to_model": str((fill["fee"] - model_fee).quantize(MICRO)),
            "sql_kalshi_fee": sql_kalshi_fee(conn, fill)}


# ---------- report ----------

def load_fills(conn, run_id):
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM run WHERE run_id = %s", (run_id,))
    if cur.fetchone() is None:
        raise ValueError(f"unknown run_id {run_id}")
    cur.execute(FILLS_SQL, (run_id,))
    names = ["outcome_id", "intent_id", "market_ticker", "side", "action", "occurred_at",
             "quantity", "price", "fee", "fee_rate"]
    return [dict(zip(names, row)) for row in cur.fetchall()]


def compare_run(conn, run_id, dump_dirs):
    fills = load_fills(conn, run_id)
    rows = []
    for fill in fills:
        folder = find_dump_folder(fill["market_ticker"], dump_dirs)
        rows.append({
            "outcome_id": fill["outcome_id"], "intent_id": fill["intent_id"],
            "market_ticker": fill["market_ticker"], "side": fill["side"], "action": fill["action"],
            "occurred_at": fill["occurred_at"].isoformat(), "quantity": str(fill["quantity"]),
            "price": format_price(fill["price"]),
            "quote": quote_check(folder, fill),
            "trades": trades_check(folder, fill),
            "fee": fee_check(conn, fill)})

    cur = conn.cursor()
    cur.execute("SELECT 1 FROM pg_proc WHERE proname = 'kalshi_fee'")
    quote_match = sum(1 for r in rows if r["quote"]["status"] == "match")
    summary = {
        "fills": len(rows),
        "quote_match": quote_match,
        "quote_other": len(rows) - quote_match,
        "trades_inside": sum(1 for r in rows if r["trades"]["position"] == "inside"),
        "trades_outside": sum(1 for r in rows if r["trades"]["position"] == "outside"),
        "no_trades": sum(1 for r in rows if r["trades"]["position"] == "no_trades"),
        "fee_formula_equal": sum(1 for r in rows if Decimal(r["fee"]["formula_cents"]) == Decimal(r["fee"]["engine"])),
        "sql_kalshi_fee_available": cur.fetchone() is not None}
    verdict = "PASS" if quote_match == len(rows) else "FAIL"
    return {"run_id": run_id, "checked_at": datetime.now(timezone.utc).isoformat(),
            "verdict": verdict, "fills": rows, "summary": summary}


def print_report(report):
    for r in report["fills"]:
        quote, trades, fee = r["quote"], r["trades"], r["fee"]
        print(f"fill {r['outcome_id']} (intent {r['intent_id']}) {r['market_ticker']} "
              f"{r['action']} {r['side']} x{r['quantity']} at {r['price']}  {r['occurred_at']}")
        print(f"  quote   {quote['status']}: bar {quote['bar_ts']} bid {quote['raw_bid']} "
              f"ask {quote['raw_ask']} -> Kalshi price {quote['kalshi_price']}")
        print(f"  trades  {trades['position']}: n {trades['n']} volume {trades['volume']} "
              f"range {trades['min_price']} to {trades['max_price']}")
        print(f"  fee     engine {fee['engine']} formula {fee['formula_cents']} model {fee['model_fee']} "
              f"diff {fee['difference_to_model']} sql {fee['sql_kalshi_fee']}")
    print(f"summary {report['summary']}")
    print(f"verdict {report['verdict']}")


def main():
    parser = argparse.ArgumentParser(description="Compare the fills of one run with Kalshi's raw data.")
    parser.add_argument("run_id", type=int)
    parser.add_argument("--dump", action="append", type=Path, help="dump dir, repeatable")
    parser.add_argument("--db", default="kalbt")
    args = parser.parse_args()
    dump_dirs = args.dump if args.dump else DEFAULT_DUMP_DIRS
    conn = connect(args.db)
    try:
        report = compare_run(conn, args.run_id, dump_dirs)
    finally:
        conn.close()
    print_report(report)
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"compare_kalshi_run{args.run_id}.json"
    path.write_text(json.dumps(report, indent=2))
    print(f"report written to {path}")
    sys.exit(0 if report["verdict"] == "PASS" else 1)


if __name__ == "__main__":
    main()
