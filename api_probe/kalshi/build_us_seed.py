"""Discover every US daily-high series with markets in the backfill window and write the seed migration.

Run: uv run python api_probe/kalshi/build_us_seed.py
Read-only on the APIs (Kalshi public API, IEM station metadata).
Writes db/migrations/0002_us_daily_high_stations.sql (stations, underlyings, series).
"""

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
IEM = "https://mesonet.agron.iastate.edu/geojson/network.py"
KALSHI_PAUSE = 0.6   # seconds after every Kalshi request (at most 2 per second)
IEM_PAUSE = 1.1      # seconds after every IEM request (at most 1 per second)
RETRY_WAIT = 10      # seconds before retrying a 429 or 5xx
MAX_RETRIES = 3

OUT_FILE = Path(__file__).resolve().parents[2] / "db" / "migrations" / "0002_us_daily_high_stations.sql"

STATION_NETWORK = {
    "ATL": "GA_ASOS", "AUS": "TX_ASOS", "BOS": "MA_ASOS", "DCA": "VA_ASOS", "DEN": "CO_ASOS",
    "DFW": "TX_ASOS", "EWR": "NJ_ASOS", "HOU": "TX_ASOS", "LAS": "NV_ASOS", "LAX": "CA_ASOS",
    "MDW": "IL_ASOS", "MIA": "FL_ASOS", "MSP": "MN_ASOS", "MSY": "LA_ASOS", "NYC": "NY_ASOS",
    "OKC": "OK_ASOS", "PHL": "PA_ASOS", "PHX": "AZ_ASOS", "SAN": "CA_ASOS", "SAT": "TX_ASOS",
    "SDF": "KY_ASOS", "SEA": "WA_ASOS", "SFO": "CA_ASOS", "TTN": "NJ_ASOS",
}
CODE = re.compile(r"\(CLI([A-Z]{3})\)")
WINDOW = (date(2026, 8, 24), date(2026, 11, 22))
EXCLUDED = ("KXHIGHUS", "KXHIGHNYD")

request_count = 0


def get(url, params, pause):
    """GET url?params and return the parsed JSON. Retries 429 and 5xx up to MAX_RETRIES times."""
    global request_count
    full_url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(full_url, headers={"User-Agent": "kalbt-probe"})
    retries = 0
    while True:
        request_count += 1
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                body = json.load(response)
            time.sleep(pause)
            return body
        except urllib.error.HTTPError as error:
            time.sleep(pause)
            if (error.code == 429 or error.code >= 500) and retries < MAX_RETRIES:
                retries += 1
                time.sleep(RETRY_WAIT)
                continue
            raise


def kalshi_get(path, params):
    return get(KALSHI + path, params, KALSHI_PAUSE)


def fetch_markets(path, series):
    """All markets of the series on one surface, following the cursor until it is empty."""
    markets = []
    cursor = ""
    while True:
        body = kalshi_get(path, {"series_ticker": series, "limit": 1000, "cursor": cursor})
        markets.extend(body.get("markets", []))
        cursor = body.get("cursor") or ""
        if not cursor:
            return markets


def event_day(market):
    """Event day from the event ticker (KXHIGHNY-26NOV22 -> 2026-11-22), or None."""
    event_ticker = market.get("event_ticker") or ""
    if "-" not in event_ticker:
        return None
    text = event_ticker.split("-", 1)[1]
    try:
        return datetime.strptime(text, "%y%b%d").date()
    except ValueError:
        return None


def window_markets(series):
    """The series' markets (live and historical, each ticker once) whose event day is in WINDOW."""
    markets = fetch_markets("/markets", series) + fetch_markets("/historical/markets", series)
    seen = set()
    kept = []
    for market in markets:
        if market["ticker"] in seen:
            continue
        seen.add(market["ticker"])
        day = event_day(market)
        if day is not None and WINDOW[0] <= day <= WINDOW[1]:
            kept.append(market)
    return kept


def sql_text(text):
    return "'" + text.replace("'", "''") + "'"


def main():
    body = kalshi_get("/series", {"category": "Climate and Weather"})
    titles = {}   # series ticker -> title
    for item in body["series"]:
        ticker = item["ticker"]
        if ticker.startswith("KXHIGH") and ticker not in EXCLUDED:
            titles[ticker] = item.get("title") or ""

    rows = []       # one dict per series: series, iem_id, markets, status
    candidates = {}  # iem_id -> [(market count, series)]
    for series in sorted(titles):
        markets = window_markets(series)
        if not markets:
            rows.append({"series": series, "iem_id": "", "markets": 0,
                         "status": "skipped: inactive (no market in window)"})
            continue
        codes = set()
        for market in markets:
            codes.update(CODE.findall(market.get("rules_primary") or ""))
        if len(codes) != 1:
            rows.append({"series": series, "iem_id": "", "markets": len(markets),
                         "status": f"skipped: unresolved station ({len(codes)} codes)"})
            continue
        iem_id = codes.pop()
        if iem_id not in STATION_NETWORK:
            rows.append({"series": series, "iem_id": iem_id, "markets": len(markets),
                         "status": "skipped: unknown station"})
            continue
        rows.append({"series": series, "iem_id": iem_id, "markets": len(markets), "status": "pending"})
        candidates.setdefault(iem_id, []).append((len(markets), series))

    # One series per station: the one with the most markets in the window.
    chosen = {}   # iem_id -> series
    for iem_id, options in candidates.items():
        chosen[iem_id] = max(options)[1]
    for row in rows:
        if row["status"] == "pending" and chosen[row["iem_id"]] != row["series"]:
            row["status"] = "skipped: duplicate series"

    # IEM metadata, one request per network.
    features = {}   # network -> {iem_id: feature}
    stations = {}   # iem_id -> dict of station fields
    for row in rows:
        if row["status"] != "pending":
            continue
        iem_id = row["iem_id"]
        network = STATION_NETWORK[iem_id]
        if network not in features:
            collection = get(IEM, {"network": network}, IEM_PAUSE)
            features[network] = {f["id"]: f for f in collection["features"]}
        feature = features[network].get(iem_id)
        if feature is None:
            row["status"] = "skipped: no IEM metadata"
            continue
        lon, lat = feature["geometry"]["coordinates"][:2]
        tz = feature["properties"]["tzname"]
        offset = int(ZoneInfo(tz).utcoffset(datetime(2026, 1, 15)).total_seconds() // 3600)
        stations[iem_id] = {
            "name": feature["properties"]["sname"], "lat": lat, "lon": lon,
            "elevation": feature["properties"]["elevation"], "tz": tz, "offset": offset,
            "network": network, "series": row["series"],
        }
        row["status"] = "seeded"
        row["name"] = stations[iem_id]["name"]

    lines = [
        "-- US daily-high stations, underlyings and series (unit A019).",
        f"-- Generated {date.today()} by api_probe/kalshi/build_us_seed.py.",
        f"-- Window: {WINDOW[0]} to {WINDOW[1]}.",
        "-- Skipped series:",
    ]
    skipped = [row for row in rows if row["status"].startswith("skipped")]
    for row in skipped:
        lines.append(f"--   {row['series']}: {row['status'][len('skipped: '):]}")
    if not skipped:
        lines.append("--   none")
    lines.append("")

    for iem_id in sorted(stations):
        s = stations[iem_id]
        name = sql_text(s["name"])
        lines.append(
            "INSERT INTO station (station_id, name, latitude, longitude, elevation_m, tz_name, "
            "std_utc_offset_h,\n"
            "                     cli_pil, iem_id, iem_network, rules_names)\n"
            f"VALUES ('K{iem_id}', {name}, {s['lat']}, {s['lon']}, {s['elevation']}, "
            f"{sql_text(s['tz'])}, {s['offset']}, 'CLI{iem_id}', '{iem_id}', '{s['network']}',\n"
            f"        ARRAY['(CLI{iem_id})']) ON CONFLICT DO NOTHING;"
        )
        description = sql_text(f"Daily maximum temperature, {s['name']} (K{iem_id})")
        lines.append(
            "INSERT INTO underlying (underlying_id, description, station_id)\n"
            f"VALUES ('{iem_id}_HIGH', {description}, 'K{iem_id}') ON CONFLICT DO NOTHING;"
        )
        lines.append(
            "INSERT INTO series (ticker, underlying_id, title)\n"
            f"VALUES ('{s['series']}', '{iem_id}_HIGH', {sql_text(titles[s['series']])}) "
            "ON CONFLICT DO NOTHING;"
        )
        lines.append("")
    OUT_FILE.write_text("\n".join(lines))

    print(f"{'series':<14}{'iem_id':<8}{'markets':>8}  {'station':<36}status")
    for row in rows:
        print(f"{row['series']:<14}{row['iem_id']:<8}{row['markets']:>8}  "
              f"{row.get('name', ''):<36}{row['status']}")
    print(f"seeded: {len(stations)}, skipped: {len(skipped)}, requests: {request_count}")


if __name__ == "__main__":
    main()
