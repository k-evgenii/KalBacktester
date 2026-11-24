"""Load the one-week weather dump (api_probe/weather/dump/<station>/) into the weather tables.

Run from the repo root: uv run python -m ingest.load_weather_dump --station KNYC
Fills official_daily (nws_cli and kalshi), observation_hourly and forecast_hourly.
Before writing, Kalshi's settled temperature is compared with the NWS high for each day;
a pattern of disagreement blocks the load (likely wrong station).
Safe to run twice: every insert is ON CONFLICT DO NOTHING.
"""
import argparse
import csv
import json
import sys
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from db.connect import connect

REPO_ROOT = Path(__file__).resolve().parent.parent
WEATHER_DIR = REPO_ROOT / "api_probe" / "weather" / "dump"
MARKETS_FILE = REPO_ROOT / "api_probe" / "kalshi" / "KXHIGHNY" / "markets.json"

# Open-Meteo variables, in the column order of the forecast_hourly INSERT below:
# temp_f, dewpoint_f, cloud_cover_pct, wind_speed_mph, precip_in, shortwave_wm2
FORECAST_VARIABLES = [
    "temperature_2m",
    "dew_point_2m",
    "cloud_cover",
    "wind_speed_10m",
    "precipitation",
    "shortwave_radiation",
]

OFFICIAL_DAILY_INSERT = (
    "INSERT INTO official_daily (station_id, climate_date, source, high_f, high_time_lst, "
    "low_f, issued_at, reference) VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING"
)


def to_decimal(value):
    return Decimal(str(value)) if value is not None else None


def csv_decimal(text):
    return None if text == "M" else Decimal(text)


def parse_utc(text):
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(timezone.utc)


def event_date(event_ticker):
    return datetime.strptime(event_ticker.split("-")[1], "%y%b%d").date()


def cli_rows(station, dump_dir):
    rows = []
    for cli_file in sorted(dump_dir.glob("cli_*.json")):
        for r in json.loads(cli_file.read_text()):
            product = r["product"]
            issued_at = datetime.strptime(product[:12], "%Y%m%d%H%M").replace(tzinfo=timezone.utc)
            rows.append((
                station,
                datetime.strptime(r["valid"], "%Y-%m-%d").date(),
                "nws_cli",
                to_decimal(r["high"]),
                r["high_time"],
                to_decimal(r["low"]),
                issued_at,
                product,
            ))
    return rows


def numeric_verdict(n_days, n_mismatches):
    """'ok', 'warn' or 'block'. Block needs at least 2 mismatched days AND more than 2 % of days."""
    if n_mismatches == 0:
        return "ok"
    if n_mismatches >= 2 and n_mismatches > 0.02 * n_days:
        return "block"
    return "warn"


def kalshi_values(markets_file):
    """{event day: expiration_value as Decimal}; all markets of an event must agree."""
    markets = json.loads(markets_file.read_text())["markets"]
    by_event = {}
    for m in markets:
        by_event.setdefault(m["event_ticker"], set()).add(Decimal(m["expiration_value"]))

    values = {}
    for event_ticker, event_values in by_event.items():
        if len(event_values) != 1:
            sys.exit(f"{event_ticker}: markets carry different expiration_value: {sorted(event_values)}")
        values[event_date(event_ticker)] = event_values.pop()
    return values


def station_check(station, dump_dir, markets_file):
    """Compare Kalshi's value with the NWS CLI high on each day both exist; exit 1 on 'block'."""
    kalshi = kalshi_values(markets_file)
    nws = {row[1]: row[3] for row in cli_rows(station, dump_dir)}

    days = sorted(day for day in kalshi if day in nws and nws[day] is not None)
    mismatches = [day for day in days if kalshi[day] != nws[day]]
    for day in mismatches:
        print(f"WARN station check: {day} kalshi {kalshi[day]} nws {nws[day]}")

    verdict = numeric_verdict(len(days), len(mismatches))
    print(f"station check: {len(days)} days compared, {len(mismatches)} mismatches -> {verdict}")
    if verdict == "block":
        print("station check: BLOCK, nothing inserted")
        sys.exit(1)


def kalshi_rows(cur, markets_file):
    markets = json.loads(markets_file.read_text())["markets"]
    by_event = {}
    for m in markets:
        by_event.setdefault(m["event_ticker"], []).append(m)

    rows = []
    for event_ticker, event_markets in sorted(by_event.items()):
        values = {Decimal(m["expiration_value"]) for m in event_markets}
        if len(values) != 1:
            sys.exit(f"{event_ticker}: markets carry different expiration_value: {sorted(values)}")
        issued_at = min(parse_utc(m["settlement_ts"]) for m in event_markets)

        series_ticker = event_ticker.split("-")[0]
        cur.execute(
            "SELECT u.station_id FROM series s JOIN underlying u "
            "ON u.underlying_id = s.underlying_id WHERE s.ticker = %s",
            (series_ticker,),
        )
        found = cur.fetchone()
        if found is None or found[0] is None:
            sys.exit(f"{event_ticker}: series {series_ticker} has no underlying with a station")

        rows.append((
            found[0], event_date(event_ticker), "kalshi", values.pop(), None, None,
            issued_at, event_ticker,
        ))
    return rows


def observation_rows(station, dump_dir):
    rows = []
    with open(dump_dir / "asos.csv", newline="") as f:
        for r in csv.DictReader(f):
            observed_at = datetime.strptime(r["valid"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
            rows.append((
                station,
                observed_at,
                csv_decimal(r["tmpf"]),
                csv_decimal(r["dwpf"]),
                csv_decimal(r["relh"]),
                csv_decimal(r["sknt"]),
                csv_decimal(r["p01i"]),
                r["metar"],
            ))
    return rows


def forecast_rows(station, model, forecast_file):
    hourly = json.loads(forecast_file.read_text())["hourly"]
    rows = []
    for i, time_text in enumerate(hourly["time"]):
        valid_time = datetime.strptime(time_text, "%Y-%m-%dT%H:%M").replace(tzinfo=timezone.utc)
        for lead in range(8):
            values = []
            for variable in FORECAST_VARIABLES:
                name = variable if lead == 0 else f"{variable}_previous_day{lead}"
                values.append(to_decimal(hourly[name][i]))
            if all(v is None for v in values):
                continue
            rows.append((station, model, valid_time, lead, *values))
    return rows


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--station", required=True)
    parser.add_argument("--dump-dir", type=Path)
    parser.add_argument("--markets", type=Path, default=MARKETS_FILE)
    args = parser.parse_args()
    station = args.station
    dump_dir = args.dump_dir or WEATHER_DIR / station
    inserted = {}

    station_check(station, dump_dir, args.markets)

    with connect() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM station WHERE station_id = %s", (station,))
        if cur.fetchone() is None:
            sys.exit(f"Station {station} is not in the station table, nothing inserted.")

        cur.executemany(OFFICIAL_DAILY_INSERT, cli_rows(station, dump_dir))
        inserted["official_daily nws_cli"] = cur.rowcount

        cur.executemany(OFFICIAL_DAILY_INSERT, kalshi_rows(cur, args.markets))
        inserted["official_daily kalshi"] = cur.rowcount

        cur.executemany(
            "INSERT INTO observation_hourly (station_id, observed_at, temp_f, dewpoint_f, "
            "rel_humidity_pct, wind_speed_kt, precip_in, metar) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
            observation_rows(station, dump_dir),
        )
        inserted["observation_hourly"] = cur.rowcount

        inserted["forecast_hourly"] = 0
        for forecast_file in sorted(dump_dir.glob("forecast_*.json")):
            model = forecast_file.stem.removeprefix("forecast_")
            cur.executemany(
                "INSERT INTO forecast_hourly (station_id, model, valid_time, lead_day, temp_f, "
                "dewpoint_f, cloud_cover_pct, wind_speed_mph, precip_in, shortwave_wm2) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s) ON CONFLICT DO NOTHING",
                forecast_rows(station, model, forecast_file),
            )
            inserted["forecast_hourly"] += cur.rowcount

        conn.commit()

    for table, count in inserted.items():
        print(f"{table}: {count} rows inserted")


if __name__ == "__main__":
    main()
