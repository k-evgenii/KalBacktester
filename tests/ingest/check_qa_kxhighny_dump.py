"""Checks the ingestion QA gate on tampered copies of the KXHIGHNY dump (spec 004, tests 1 to 14).

Every test copies the real dump into a temporary folder, makes one change, and runs run_checks
against the live database. The real dump and the database are only read.
"""
import json
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from db.connect import connect
from ingest.qa_kxhighny_dump import run_checks

ROOT = Path(__file__).resolve().parent.parent.parent
DUMP = Path("/home/kondr/KalBacktester/api_probe/kalshi/KXHIGHNY")
TABLES = ["underlying", "series", "event", "market", "candle",
          "run", "order_intent", "order_outcome", "settlement", "position_snapshot"]

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def row_counts():
    count_conn = connect()
    cur = count_conn.cursor()
    counts = {}
    for table in TABLES:
        cur.execute(f"SELECT count(*) FROM {table}")
        counts[table] = cur.fetchone()[0]
    count_conn.close()
    return counts


def copy_dump(tmp):
    """Copy the real dump (without report files) into tmp and return the copy's path."""
    copy = Path(tmp) / "KXHIGHNY"
    shutil.copytree(DUMP, copy, ignore=shutil.ignore_patterns("_report.json", "_qa_report.json"))
    return copy


def load_json(path):
    return json.loads(path.read_text())


def save_json(path, data):
    path.write_text(json.dumps(data))


def markets_of(copy):
    data = load_json(copy / "markets.json")
    return data, data["markets"]


def bars_path(copy, ticker):
    return copy / "bars_1min" / (ticker + ".json")


def time_text(ts):
    """Minute-resolution text of an end_period_ts, as it should appear in an example."""
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M")


def first_bar_index(bars, condition):
    for i, bar in enumerate(bars):
        if condition(bar):
            return i
    raise RuntimeError("no bar in the dump satisfies the condition")


def checks_by_id(rep):
    return {c["id"]: c for c in rep["checks"]}


def failed(rep, check_id):
    check = checks_by_id(rep).get(check_id)
    return check is not None and check["status"] == "fail"


def expect_block(name, rep, check_ids):
    """The verdict is BLOCK and every listed check failed."""
    statuses = {i: checks_by_id(rep).get(i, {}).get("status") for i in check_ids}
    ok = rep["verdict"] == "BLOCK" and all(s == "fail" for s in statuses.values())
    report(name, ok, f"verdict {rep['verdict']}, statuses {statuses}")


def tamper_test3(copy):
    """Test 3 change: delete the bars file of the first market. Returns the ticker."""
    _, markets = markets_of(copy)
    ticker = markets[0]["ticker"]
    bars_path(copy, ticker).unlink()
    return ticker


conn = connect()
counts_before = row_counts()

# Test 1: untouched copy
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    rep = run_checks(copy, conn)
    warns = [c["id"] for c in rep["checks"] if c["severity"] == "WARN" and c["status"] == "fail"]
    print(f"  untouched copy: verdict {rep['verdict']}, failed WARN checks {warns}")
    ids = [c["id"] for c in rep["checks"]]
    expected_ids = list(range(1, 8)) + list(range(9, 17)) + list(range(18, 22))
    valid = all(c["severity"] in ("BLOCK", "WARN") and c["status"] in ("pass", "fail", "skipped")
                for c in rep["checks"])
    report("1 untouched copy is PASS and the report lists every check once",
           rep["verdict"] == "PASS" and ids == expected_ids and valid,
           f"verdict {rep['verdict']}, ids {ids}, valid {valid}, blocking {[c['id'] for c in rep['checks'] if c['status'] == 'fail' and c['severity'] == 'BLOCK']}")

# Test 2: remove all markets of one event
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    data, markets = markets_of(copy)
    event = markets[0]["event_ticker"]
    data["markets"] = [m for m in markets if m["event_ticker"] != event]
    save_json(copy / "markets.json", data)
    print(f"  removed {len(markets) - len(data['markets'])} markets of event {event}")
    expect_block("2 missing event is BLOCK, check 2", run_checks(copy, conn), [2])

# Test 3: delete one bars file
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    ticker = tamper_test3(copy)
    print(f"  deleted bars_1min/{ticker}.json")
    expect_block("3 missing bars file is BLOCK, check 3", run_checks(copy, conn), [3])

# Test 4: yes_ask close_dollars = "abc"
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    _, markets = markets_of(copy)
    ticker = markets[0]["ticker"]
    path = bars_path(copy, ticker)
    data = load_json(path)
    i = first_bar_index(data["candlesticks"], lambda b: "close_dollars" in b["yes_ask"])
    data["candlesticks"][i]["yes_ask"]["close_dollars"] = "abc"
    save_json(path, data)
    print(f"  {ticker} bar {i}: yes_ask.close_dollars = 'abc'")
    expect_block("4 non-numeric ask is BLOCK, check 5", run_checks(copy, conn), [5])

# Test 5: rename close_dollars to closing in yes_bid
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    _, markets = markets_of(copy)
    ticker = markets[0]["ticker"]
    path = bars_path(copy, ticker)
    data = load_json(path)
    i = first_bar_index(data["candlesticks"], lambda b: "close_dollars" in b["yes_bid"])
    bid = data["candlesticks"][i]["yes_bid"]
    bid["closing"] = bid.pop("close_dollars")
    save_json(path, data)
    print(f"  {ticker} bar {i}: yes_bid.close_dollars renamed to closing")
    expect_block("5 bid with no close key is BLOCK, check 6", run_checks(copy, conn), [6])

# Test 6: flip a losing market's result to yes
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    data, markets = markets_of(copy)
    losing = [m for m in markets if m.get("result") == "no" and m.get("expiration_value")]
    losing[0]["result"] = "yes"
    save_json(copy / "markets.json", data)
    print(f"  {losing[0]['ticker']}: result no -> yes (expiration_value {losing[0]['expiration_value']})")
    expect_block("6 flipped result is BLOCK, checks 10 and 13", run_checks(copy, conn), [10, 13])

# Test 7: swap two consecutive bars
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    _, markets = markets_of(copy)
    ticker = markets[0]["ticker"]
    path = bars_path(copy, ticker)
    data = load_json(path)
    bars = data["candlesticks"]
    bars[0], bars[1] = bars[1], bars[0]
    save_json(path, data)
    print(f"  {ticker}: bars 0 and 1 swapped")
    expect_block("7 swapped bars is BLOCK, check 11", run_checks(copy, conn), [11])

# Test 8: lower a between market's floor_strike by 1 so the ladder breaks (check 7 stays clean)
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    data, markets = markets_of(copy)
    event = markets[0]["event_ticker"]
    betweens = sorted((m for m in markets if m["event_ticker"] == event and m["strike_type"] == "between"),
                      key=lambda m: m["floor_strike"])
    target = betweens[1]
    old_floor = target["floor_strike"]
    target["floor_strike"] = old_floor - 1
    save_json(copy / "markets.json", data)
    print(f"  {target['ticker']}: floor_strike {old_floor} -> {target['floor_strike']} (cap_strike {target['cap_strike']})")
    expect_block("8 broken ladder is BLOCK, check 12", run_checks(copy, conn), [12])

# Test 9: change one bar's volume_fp in a market that is in the database
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    _, markets = markets_of(copy)
    cur = conn.cursor()
    ticker = None
    for m in markets:
        cur.execute("SELECT count(*) FROM candle WHERE market_ticker = %s", (m["ticker"],))
        if cur.fetchone()[0] > 0:
            ticker = m["ticker"]
            break
    conn.rollback()
    path = bars_path(copy, ticker)
    data = load_json(path)
    bar = data["candlesticks"][0]
    old_volume = bar["volume_fp"]
    bar["volume_fp"] = "123456.00"
    save_json(path, data)
    print(f"  {ticker} bar 0 ({time_text(bar['end_period_ts'])}): volume_fp {old_volume} -> 123456.00")
    rep = run_checks(copy, conn)
    examples = checks_by_id(rep).get(16, {}).get("examples", [])
    names_bar = any(ticker in e and time_text(bar["end_period_ts"]) in e for e in examples)
    report("9 changed volume in a loaded market is BLOCK, check 16 names market and time",
           rep["verdict"] == "BLOCK" and failed(rep, 16) and names_bar,
           f"verdict {rep['verdict']}, check 16 {checks_by_id(rep).get(16)}")

# Test 10: row counts unchanged by tests 1 to 9
counts_after = row_counts()
report("10 row counts of all 10 tables unchanged after tests 1 to 9", counts_before == counts_after,
       f"{counts_before} vs {counts_after}")

# Test 11: the loader refuses a dump whose gate says BLOCK
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    ticker = tamper_test3(copy)
    before = row_counts()
    proc = subprocess.run(["uv", "run", "python", "-m", "ingest.load_kxhighny_dump", str(copy)],
                          cwd=ROOT, capture_output=True, text=True)
    after = row_counts()
    print(f"  loader on copy without bars of {ticker}: exit {proc.returncode}")
    print("  " + (proc.stdout.strip().splitlines() or ["(no stdout)"])[-1])
    report("11a loader exits non-zero on a BLOCK dump", proc.returncode != 0, f"exit {proc.returncode}")
    report("11b loader inserts nothing on a BLOCK dump", before == after, f"{before} vs {after}")
    report("11c loader stdout says the gate blocked", "QA gate: BLOCK" in proc.stdout, proc.stdout.strip()[-200:])

# Test 12: market status active and result empty
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    data, markets = markets_of(copy)
    target = markets[0]
    old = (target["status"], target.get("result"))
    target["status"] = "active"
    target["result"] = ""
    save_json(copy / "markets.json", data)
    print(f"  {target['ticker']}: status/result {old} -> ('active', '')")
    expect_block("12 unsettled market is BLOCK, check 9", run_checks(copy, conn), [9])

# Test 13: a crossed quote (bid above ask, both strictly between 0 and 1) is only a WARN on check 11.
# The crossed bar is a NEW bar inserted at an empty minute, so it is not in the database and
# check 16 ignores it. Its volume is 0, so check 19 stays clean.
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    _, markets = markets_of(copy)
    ticker = markets[0]["ticker"]
    path = bars_path(copy, ticker)
    data = load_json(path)
    bars = data["candlesticks"]
    i = next(k for k in range(len(bars) - 1)
             if bars[k + 1]["end_period_ts"] - bars[k]["end_period_ts"] >= 120)
    new_ts = bars[i]["end_period_ts"] + 60
    new_bar = {"end_period_ts": new_ts, "price": {}, "volume_fp": "0.00",
               "yes_bid": {"close_dollars": "0.7000"}, "yes_ask": {"close_dollars": "0.3000"}}
    bars.insert(i + 1, new_bar)
    save_json(path, data)
    print(f"  {ticker}: new bar inserted after bar {i} at {time_text(new_ts)} (bid 0.7000, ask 0.3000, volume 0.00)")
    rep = run_checks(copy, conn)
    check11 = checks_by_id(rep).get(11, {})
    names_bar = any(ticker in e and time_text(new_ts) in e for e in check11.get("examples", []))
    report("13 crossed quotes is PASS with a WARN on check 11 naming market and time",
           rep["verdict"] == "PASS" and check11.get("status") == "fail" and check11.get("severity") == "WARN"
           and names_bar,
           f"verdict {rep['verdict']}, check 11 {check11}")

# Test 14: remove volume_fp from one bar
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    _, markets = markets_of(copy)
    ticker = markets[0]["ticker"]
    path = bars_path(copy, ticker)
    data = load_json(path)
    i = first_bar_index(data["candlesticks"], lambda b: "volume_fp" in b and "volume" not in b)
    del data["candlesticks"][i]["volume_fp"]
    save_json(path, data)
    print(f"  {ticker} bar {i}: volume_fp removed")
    expect_block("14 bar without volume is BLOCK, check 6", run_checks(copy, conn), [6])

# Test 15 (extra): an unsettled market with no result and no settlement_ts keys must not crash the gate
with tempfile.TemporaryDirectory() as tmp:
    copy = copy_dump(tmp)
    data, markets = markets_of(copy)
    target = markets[0]
    target["status"] = "active"
    target.pop("result", None)
    target.pop("settlement_ts", None)
    save_json(copy / "markets.json", data)
    print(f"  {target['ticker']}: status -> 'active', result and settlement_ts keys deleted")
    try:
        rep = run_checks(copy, conn)
        expect_block("15 unsettled market with missing keys is BLOCK, check 9, no crash", rep, [9])
    except Exception as error:
        conn.rollback()
        report("15 unsettled market with missing keys is BLOCK, check 9, no crash", False,
               f"run_checks raised {type(error).__name__}: {error}")

conn.rollback()
conn.close()
print(f"{len(failures)} failed")
raise SystemExit(1 if failures else 0)
