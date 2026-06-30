"""
weather.py — official daily-max temperature from the NWS CLI product (via IEM).

Why CLI (and not station observations): Kalshi settles these temperature
markets on the NWS Climatological Report daily MAXIMUM. Hourly station obs are
sparse/empty for Central Park (KNYC) and aren't the settlement number anyway.

Source of history: the Iowa Environmental Mesonet (IEM) archive keeps every CLI
ever issued, queryable by PIL + date. NWS api.weather.gov only keeps ~2 weeks.

Everything is UTC-internal. The CLI "time of max" is Local STANDARD Time (an NWS
quirk — standard year-round, so in summer it is one hour off the wall clock).
We convert it with the city's fixed standard offset and tag a confidence; the
gate treats the max VALUE as truth and the TIME as advisory (value-first).

SAFETY (fail closed). A date is BLOCKED — no entries allowed — unless ALL hold:
  - the report's "SUMMARY FOR <date>" header equals the requested date
  - the station name matches the city's expected station
  - the MAXIMUM value parses to an int in a sane range (20..130 F)
  - the internal cross-check passes: max - normal == departure (when present)
Typos like `95R` (record flag), `320 PM` (missing colon), and `MM` (missing)
are handled explicitly; anything unrecognised blocks rather than guesses.

This module is network-light and import-safe. The live fetch (fetch_daily_max)
runs on a machine with internet; parse_cli_text is pure and unit-tested.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone

UTC = timezone.utc

# city → CLI product PIL (the issuedby code from Kalshi's settlement source)
CITY_CLI = {
    "new_york":    "CLINYC",
    "los_angeles": "CLILAX",
    "miami":       "CLIMIA",
}
# substring that MUST appear in the report's station-name header
CITY_STATION = {
    "new_york":    "CENTRAL PARK",
    "los_angeles": "LOS ANGELES INTL AIRPORT",
    "miami":       "MIAMI",
}
# hours to ADD to Local Standard Time to get UTC (CLI time is LST year-round)
CITY_STD_OFFSET_H = {
    "new_york":    5,   # EST
    "miami":       5,   # EST
    "los_angeles": 8,   # PST
}
MONTHS = {m: i for i, m in enumerate(
    ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY",
     "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"], start=1)}

IEM_LIST = "https://mesonet.agron.iastate.edu/api/1/nws/afos/list.json?pil={pil}&date={d}"
SANE_MIN, SANE_MAX = 20, 130
UA = {"User-Agent": "kbt-weather (contact: you@example.com)"}


# ---------------------------------------------------------------------------
# Record
# ---------------------------------------------------------------------------

@dataclass
class DailyMax:
    city:            str
    date:            str            # ISO date the report is FOR
    status:          str            # "OK" | "BLOCKED"
    max_f:           int | None = None
    max_time_utc:    str | None = None     # ISO, or None if time was dirty
    time_confidence: str = "none"          # "clean" | "assumed" | "none"
    record_flag:     bool = False          # the CLI 'R' (record tied/set)
    station:         str | None = None
    source_id:       str | None = None
    reason:          str | None = None     # why BLOCKED

    def as_dict(self) -> dict:
        return asdict(self)


def _block(city, d: date, reason: str, **extra) -> DailyMax:
    return DailyMax(city=city, date=d.isoformat(), status="BLOCKED", reason=reason, **extra)


# ---------------------------------------------------------------------------
# Pure parser (unit-tested; no network)
# ---------------------------------------------------------------------------

def _int_or_none(tok: str) -> int | None:
    t = tok.strip().rstrip("Rr")
    if t.lstrip("-").isdigit():
        return int(t)
    return None


def _parse_time(time_tok: str, ampm: str) -> tuple[int | None, int | None, str]:
    """'320'/'PM' -> (15,20,'assumed');  '12:50'/'PM' -> (12,50,'clean')."""
    if ":" in time_tok:
        hh_s, mm_s = time_tok.split(":", 1)
        conf = "clean"
    else:
        digits = re.sub(r"\D", "", time_tok)
        if not digits:
            return None, None, "none"
        if len(digits) <= 2:
            hh_s, mm_s = digits, "00"
        else:
            hh_s, mm_s = digits[:-2], digits[-2:]
        conf = "assumed"
    if not (hh_s.isdigit() and mm_s.isdigit()):
        return None, None, "none"
    hh, mm = int(hh_s), int(mm_s)
    ampm = ampm.upper()
    if ampm == "PM" and hh != 12:
        hh += 12
    elif ampm == "AM" and hh == 12:
        hh = 0
    if not (0 <= hh <= 23 and 0 <= mm <= 59):
        return None, None, "none"
    return hh, mm, conf


def parse_cli_text(text: str, city: str, expected_date: date) -> DailyMax:
    """Parse + validate one CLI product. Fail closed on anything suspicious."""
    if city not in CITY_CLI:
        return _block(city, expected_date, f"unknown city {city!r}")

    # 1) header date
    hm = re.search(r"CLIMATE SUMMARY FOR\s+([A-Z]+)\s+(\d{1,2})\s+(\d{4})", text)
    if not hm:
        return _block(city, expected_date, "no 'CLIMATE SUMMARY FOR' header")
    mon = MONTHS.get(hm.group(1))
    if mon is None:
        return _block(city, expected_date, f"bad month {hm.group(1)!r}")
    try:
        hdr_date = date(int(hm.group(3)), mon, int(hm.group(2)))
    except ValueError:
        return _block(city, expected_date, "header date invalid")
    if hdr_date != expected_date:
        return _block(city, expected_date,
                      f"date mismatch: report is for {hdr_date}, requested {expected_date}")

    # 2) station name
    sm = re.search(r"THE\s+(.+?)\s+CLIMATE SUMMARY FOR", text)
    station = sm.group(1).strip() if sm else None
    if not station or CITY_STATION[city] not in station:
        return _block(city, expected_date,
                      f"station mismatch: got {station!r}, expected to contain {CITY_STATION[city]!r}")

    # 3) the observed YESTERDAY MAXIMUM line (has a clock time; not the normals line)
    obs_line = None
    for ln in text.splitlines():
        s = ln.strip()
        if s.startswith("MAXIMUM") and ("AM" in s or "PM" in s) and "TEMPERATURE" not in s:
            obs_line = s
            break
    if obs_line is None:
        return _block(city, expected_date, "no observed MAXIMUM line found", station=station)

    tokens = obs_line.split()
    # tokens[0]=MAXIMUM, tokens[1]=value (maybe '95R' or 'MM')
    if len(tokens) < 2:
        return _block(city, expected_date, "MAXIMUM line too short", station=station)
    raw_val = tokens[1]
    record_flag = raw_val.upper().endswith("R")
    max_f = _int_or_none(raw_val)
    if max_f is None:
        return _block(city, expected_date, f"max value not numeric: {raw_val!r}", station=station)
    if not (SANE_MIN <= max_f <= SANE_MAX):
        return _block(city, expected_date, f"max out of sane range: {max_f}", station=station)

    # time + trailing columns (record, year, normal, departure, last)
    ampm_idx = next((i for i, t in enumerate(tokens) if t.upper() in ("AM", "PM")), None)
    if ampm_idx is not None and ampm_idx >= 1:
        hh, mm, conf = _parse_time(tokens[ampm_idx - 1], tokens[ampm_idx])
        rest = tokens[ampm_idx + 1:]
    else:
        hh = mm = None
        conf = "none"
        rest = tokens[2:]

    # 4) internal consistency cross-check: observed - normal == departure
    normal    = _int_or_none(rest[2]) if len(rest) > 2 else None
    departure = _int_or_none(rest[3]) if len(rest) > 3 else None
    if normal is not None and departure is not None:
        if abs((max_f - normal) - departure) > 1:
            return _block(city, expected_date,
                          f"consistency check failed: {max_f} - {normal} != {departure}",
                          station=station)

    # build UTC time from LST (advisory)
    max_time_utc = None
    if hh is not None:
        local = datetime(expected_date.year, expected_date.month, expected_date.day, hh, mm)
        max_time_utc = (local + timedelta(hours=CITY_STD_OFFSET_H[city])).replace(tzinfo=UTC).isoformat()

    return DailyMax(
        city=city, date=expected_date.isoformat(), status="OK",
        max_f=max_f, max_time_utc=max_time_utc, time_confidence=conf,
        record_flag=record_flag, station=station,
    )


# ---------------------------------------------------------------------------
# Live fetch (runs where there is internet)
# ---------------------------------------------------------------------------

def _get_json(url: str, timeout: int = 25) -> dict:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def _get_text(url: str, timeout: int = 25) -> str:
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def fetch_daily_max(city: str, d: date) -> DailyMax:
    """
    Resolve the official daily MAX for `city` on local date `d`.

    `d`'s max is reported in the CLI issued the MORNING OF d+1, so we list
    products for d+1, then take the issuance whose header verifies as 'FOR d'
    (earliest/morning-final first). The header check enforces 'no messed dates'.
    """
    if city not in CITY_CLI:
        return _block(city, d, f"unknown city {city!r}")
    pil = CITY_CLI[city]
    qdate = (d + timedelta(days=1)).isoformat()
    try:
        listing = _get_json(IEM_LIST.format(pil=pil, d=qdate))
    except Exception as e:
        return _block(city, d, f"IEM list fetch failed: {e}")

    rows = listing.get("data") or []
    if not rows:
        return _block(city, d, f"no CLI issued for {pil} on {qdate}")

    # earliest issuance first = the morning-final report
    rows = sorted(rows, key=lambda r: r.get("entered", ""))
    last_reason = "no issuance matched the requested date"
    for row in rows:
        link = row.get("text_link")
        if not link:
            continue
        try:
            text = _get_text(link)
        except Exception as e:
            last_reason = f"text fetch failed: {e}"
            continue
        rec = parse_cli_text(text, city, d)
        rec.source_id = row.get("product_id")
        if rec.status == "OK":
            return rec
        # if it blocked only on a date mismatch, try the next issuance
        last_reason = rec.reason or last_reason
        if rec.reason and "date mismatch" not in rec.reason:
            return rec  # a real data problem, not a wrong-issuance pick
    return _block(city, d, last_reason)
