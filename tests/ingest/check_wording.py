"""Checks the rules-wording unit (spec 015, tests 1, 4 and 5).

Test 1 runs settlement_source on four strings. Tests 4 and 5 compare the live database with the
three real dumps. Expectations come from the dumps and the spec, not from the loader. Read-only.
"""
import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from db.connect import connect
from ingest.load_kxhighny_dump import settlement_source

ROOT = Path(__file__).resolve().parent.parent.parent
DUMPS = [ROOT / "api_probe" / "kalshi" / "KXHIGHNY",
         ROOT / "api_probe" / "kalshi" / "KXHIGHNY_2026-06-01_2026-06-07",
         ROOT / "api_probe" / "kalshi" / "KXHIGHNY_2026-08-12_2026-08-18"]
TWC = "The Weather Company"
NWS = "National Weather Service"
FIRST_TWC_DAY = date(2026, 8, 14)

failures = []


def report(name, ok, detail=""):
    print(("[PASS] " if ok else "[FAIL] ") + name + (" " + detail if detail and not ok else ""))
    if not ok:
        failures.append(name)


def event_day(event_ticker):
    return datetime.strptime(event_ticker.split("-", 1)[1], "%y%b%d").date()


# Test 1
report("1a only The Weather Company -> TWC",
       settlement_source("according to The Weather Company, then Yes") == TWC)
report("1b only National Weather Service -> NWS",
       settlement_source("according to the National Weather Service, then Yes") == NWS)
report("1c both -> None",
       settlement_source("The Weather Company or the National Weather Service") is None)
report("1d neither -> None", settlement_source("according to AccuWeather") is None)

markets = []
for dump in DUMPS:
    markets += json.loads((dump / "markets.json").read_text())["markets"]
dump_events = sorted({m["event_ticker"] for m in markets})

conn = connect()
cur = conn.cursor()

# Test 4
cur.execute("SELECT ticker, event_date, settlement_source FROM event")
db_events = {r[0]: (r[1], r[2]) for r in cur.fetchall()}
report("4a 21 events in the database", len(db_events) == 21, str(len(db_events)))
report("4b the database events are exactly the dump events", set(db_events) == set(dump_events),
       f"only db {sorted(set(db_events) - set(dump_events))}, only dumps {sorted(set(dump_events) - set(db_events))}")
bad = []
for ticker, (day, source) in db_events.items():
    expected = TWC if day >= FIRST_TWC_DAY else NWS
    if source != expected:
        bad.append((ticker, day, source, expected))
report("4c source per event date (NWS before 2026-08-14, TWC from then on)", not bad, str(bad[:3]))
n_nws = sum(1 for day, _ in db_events.values() if day < FIRST_TWC_DAY)
n_twc = len(db_events) - n_nws
print(f"  events: {n_nws} National Weather Service, {n_twc} The Weather Company")
report("4d event_date equals the date in the event ticker",
       all(day == event_day(t) for t, (day, _) in db_events.items()))

# Test 5
cur.execute("SELECT ticker, event_ticker, result FROM market")
db_markets = {r[0]: r[1:] for r in cur.fetchall()}
by_event = {}
for ticker, (event, _) in db_markets.items():
    by_event.setdefault(event, []).append(ticker)
bad_count = {e: len(t) for e, t in by_event.items() if len(t) != 6}
report("5a every event has exactly 6 markets", not bad_count and set(by_event) == set(dump_events),
       str(bad_count))


def yes_by_strike_rule(m):
    value = Decimal(m["expiration_value"])
    floor = Decimal(str(m["floor_strike"])) if m.get("floor_strike") is not None else None
    cap = Decimal(str(m["cap_strike"])) if m.get("cap_strike") is not None else None
    if m["strike_type"] == "less":
        return value < cap
    if m["strike_type"] == "greater":
        return value > floor
    return floor <= value <= cap


bad = []
yes_per_event = {}
for m in markets:
    got = db_markets.get(m["ticker"])
    if got is None:
        bad.append((m["ticker"], "missing in database"))
        continue
    expected_yes = yes_by_strike_rule(m)
    if (got[1] == "yes") != expected_yes:
        bad.append((m["ticker"], got[1], m["expiration_value"], m["strike_type"]))
    if got[1] == "yes":
        yes_per_event[got[0]] = yes_per_event.get(got[0], 0) + 1
report("5b database result agrees with the strike rule on expiration_value", not bad, str(bad[:3]))
bad = {e: yes_per_event.get(e, 0) for e in dump_events if yes_per_event.get(e, 0) != 1}
report("5c exactly one yes market per event", not bad, str(bad))
bad = [m["ticker"] for m in markets if m.get("result") != db_markets[m["ticker"]][1]]
report("5d database result equals the dump result", not bad, str(bad[:3]))

conn.rollback()
conn.close()
print(f"{len(failures)} failed")
raise SystemExit(1 if failures else 0)
