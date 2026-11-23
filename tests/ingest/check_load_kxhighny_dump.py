"""Checks the loaded KXHIGHNY dump against the raw dump files (spec 003, tests 1 to 6)."""
import json
import subprocess
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect

ROOT = Path(__file__).resolve().parent.parent.parent
DUMP = ROOT / "api_probe" / "kalshi" / "KXHIGHNY"
TABLES = ["underlying", "series", "event", "market", "candle"]

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def parse_time(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00"))


def closing(bar, side):
    quote = bar.get(side) or {}
    value = quote.get("close_dollars", quote.get("close"))
    return Decimal(str(value)) if value is not None else None


def expected_quotes(bar):
    bid = closing(bar, "yes_bid")
    ask = closing(bar, "yes_ask")
    if bid is not None and bid <= 0:
        bid = None
    if ask is not None and ask >= 1:
        ask = None
    return bid, ask


def dec_or_none(value):
    return None if value is None else Decimal(str(value))


def row_counts(cur):
    counts = {}
    for table in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    return counts


markets = json.loads((DUMP / "markets.json").read_text())["markets"]
bars = {}
for m in markets:
    path = DUMP / "bars_1min" / (m["ticker"] + ".json")
    bars[m["ticker"]] = json.loads(path.read_text())["candlesticks"]
total_bars = sum(len(b) for b in bars.values())
n_events = len({m["event_ticker"] for m in markets})

conn = connect()
cur = conn.cursor()

# Test 1
counts = row_counts(cur)
report("1 row counts", counts == {"underlying": 1, "series": 1, "event": 7, "market": 42, "candle": 41158},
       str(counts))
report("1 counts match dump (derived)",
       counts["event"] == n_events and counts["market"] == len(markets) and counts["candle"] == total_bars,
       f"{counts} vs events={n_events} markets={len(markets)} bars={total_bars}")

# Test 2
cur.execute("SELECT market_ticker, count(*), sum(volume) FROM candle GROUP BY market_ticker")
db_vol = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
bad = []
for ticker, bl in bars.items():
    exp = (len(bl), sum((Decimal(str(b.get("volume_fp", b.get("volume")) or "0")) for b in bl), Decimal("0")))
    if db_vol.get(ticker) != exp:
        bad.append((ticker, db_vol.get(ticker), exp))
report("2 per-market candle count and exact volume sum", not bad and set(db_vol) == set(bars), str(bad[:3]))

# Test 3
cur.execute("SELECT market_ticker, ts, yes_bid, yes_ask FROM candle")
db_candles = {(r[0], r[1]): (r[2], r[3]) for r in cur.fetchall()}
null_bid_bad, null_ask_bad, value_bad = [], [], []
tot_nb = tot_na = 0
for ticker, bl in bars.items():
    exp_nb = exp_na = 0
    db_nb = db_na = 0
    for b in bl:
        ts = datetime.fromtimestamp(b["end_period_ts"], tz=timezone.utc)
        bid, ask = expected_quotes(b)
        exp_nb += bid is None
        exp_na += ask is None
        got = db_candles.get((ticker, ts))
        if got is None:
            value_bad.append((ticker, ts, "missing"))
            continue
        db_nb += got[0] is None
        db_na += got[1] is None
        if got != (bid, ask):
            value_bad.append((ticker, ts, got, (bid, ask)))
    tot_nb += exp_nb
    tot_na += exp_na
    if db_nb != exp_nb:
        null_bid_bad.append((ticker, db_nb, exp_nb))
    if db_na != exp_na:
        null_ask_bad.append((ticker, db_na, exp_na))
report("3a NULL yes_bid count per market", not null_bid_bad, str(null_bid_bad[:3]))
report("3b NULL yes_ask count per market", not null_ask_bad, str(null_ask_bad[:3]))
report("3c totals 8107 NULL bids and 620 NULL asks", (tot_nb, tot_na) == (8107, 620), f"{tot_nb}, {tot_na}")
report("3d every candle quote equals the bar", not value_bad, str(value_bad[:3]))

# Test 4
cur.execute("""SELECT count(*) FROM candle c JOIN market m ON m.ticker = c.market_ticker
               WHERE c.ts < m.open_time OR c.ts > m.close_time""")
outside = cur.fetchone()[0]
report("4 candle ts within open_time..close_time", outside == 0, f"{outside} outside")
out_dump = 0
for m in markets:
    lo, hi = parse_time(m["open_time"]), parse_time(m["close_time"])
    for b in bars[m["ticker"]]:
        if not lo <= datetime.fromtimestamp(b["end_period_ts"], tz=timezone.utc) <= hi:
            out_dump += 1
report("4 EXTRA dump bars within the window (expectation sanity)", out_dump == 0, f"{out_dump} outside")

# Test 5
cur.execute("""SELECT ticker, strike_type, floor_strike, cap_strike, open_time, close_time, result, settled_at
               FROM market""")
db_markets = {r[0]: r[1:] for r in cur.fetchall()}
bad = []
for m in markets:
    exp = (m.get("strike_type"), dec_or_none(m.get("floor_strike")), dec_or_none(m.get("cap_strike")),
           parse_time(m["open_time"]), parse_time(m["close_time"]),
           m.get("result") or None,
           parse_time(m["settlement_ts"]) if m.get("settlement_ts") else None)
    if db_markets.get(m["ticker"]) != exp:
        bad.append((m["ticker"], db_markets.get(m["ticker"]), exp))
report("5 market attributes equal markets.json", not bad and set(db_markets) == {m["ticker"] for m in markets},
       str(bad[:2]))

# EXTRA: event and static rows
cur.execute("SELECT ticker, series_ticker, underlying_id, event_date, settlement_source FROM event")
db_events = {r[0]: r[1:] for r in cur.fetchall()}
exp_events = {}
for m in markets:
    et = m["event_ticker"]
    day = datetime.strptime(et.split("-", 1)[1], "%y%b%d").date()
    exp_events[et] = ("KXHIGHNY", "NYC_HIGH", day, "The Weather Company")
report("EXTRA event rows (date, series, underlying, source)", db_events == exp_events, str(db_events))
cur.execute("SELECT underlying_id, description FROM underlying")
u = cur.fetchall()
report("EXTRA underlying row", u == [("NYC_HIGH", "Daily maximum temperature, New York Central Park (KNYC)")], str(u))
cur.execute("SELECT ticker, underlying_id, title FROM series")
s = cur.fetchall()
report("EXTRA series row", s == [("KXHIGHNY", "NYC_HIGH", "Highest temperature in NYC")], str(s))

before = row_counts(cur)
conn.rollback()
conn.close()

# Test 6
proc = subprocess.run([sys.executable, "-m", "ingest.load_kxhighny_dump"], cwd=ROOT,
                      capture_output=True, text=True)
print("--- loader rerun output ---")
print(proc.stdout.strip())
print(proc.stderr.strip())
print("---------------------------")
report("6a rerun exits 0", proc.returncode == 0, f"exit {proc.returncode}")
out = proc.stdout.lower()
lines_by_table = {t: [ln for ln in out.splitlines() if t in ln.split()[0:2] or ln.strip().startswith(t)]
                  for t in TABLES}
zero_ok = True
for t in TABLES:
    ls = lines_by_table[t]
    # a line for the table must exist and must contain a 0 and no other nonzero integer
    if not ls:
        zero_ok = False
        continue
    for ln in ls:
        nums = [tok for tok in ln.replace(":", " ").replace("=", " ").split() if tok.isdigit()]
        if not nums or any(int(n) != 0 for n in nums):
            zero_ok = False
report("6b rerun reports 0 rows inserted for every table", zero_ok, str(lines_by_table))
conn = connect()
cur = conn.cursor()
after = row_counts(cur)
conn.close()
report("6c row counts unchanged after rerun", before == after, f"{before} vs {after}")

print(f"{len(failures)} failed")
sys.exit(1 if failures else 0)
