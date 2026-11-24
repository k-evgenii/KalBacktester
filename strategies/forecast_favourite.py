"""Reference strategy: buy YES on the bracket that contains the average forecast daily high.

Run from the repo root:
    uv run python -m strategies.forecast_favourite --series KXHIGHNY --start 2026-11-16 \
        --end 2026-11-22 --out intents.csv [--db NAME] [--quantity N]
Rules are in .claude/specs/B016-forecast-favourite-strategy.md. SELECT only.
"""
import argparse
import csv
from datetime import date, datetime, time, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal

from db.connect import connect

DEFAULTS = {"decision_time_lst": "09:00", "lead_day": 1, "min_models": 5, "quantity": 10}

CSV_COLUMNS = ["decided_at", "market_ticker", "side", "action", "quantity", "reason"]


def intents_for_event(conn, event_ticker, params):
    """Return (intents, info): 0 or 1 intent in the engine's format, and a summary dict."""
    info = {
        "event_ticker": event_ticker,
        "decided_at": None,
        "n_models": 0,
        "forecast_high": None,
        "chosen_market": None,
        "skipped": None,
    }
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.event_date, s.station_id, s.std_utc_offset_h
            FROM event e
            JOIN underlying u ON u.underlying_id = e.underlying_id
            JOIN station s ON s.station_id = u.station_id
            WHERE e.ticker = %s
            """,
            (event_ticker,),
        )
        row = cur.fetchone()
        if row is None:
            info["skipped"] = "no_station"
            return [], info
        event_date, station, offset = row

        # decision time in UTC, from local standard time
        h, m = map(int, params["decision_time_lst"].split(":"))
        decided_at = datetime.combine(event_date, time(h, m), tzinfo=timezone.utc) - timedelta(hours=offset)
        info["decided_at"] = decided_at

        # climate day window in UTC
        lead = params["lead_day"]
        day_start = datetime.combine(event_date, time(0), tzinfo=timezone.utc) - timedelta(hours=offset)
        day_end = day_start + timedelta(hours=24)

        # no look-ahead: the last hour of the day must have been predicted before the decision
        last_prediction = day_end - timedelta(hours=1) - timedelta(hours=24 * lead)
        if last_prediction > decided_at:
            info["skipped"] = "lookahead"
            return [], info

        cur.execute(
            """
            SELECT model, max(temp_f) AS model_high, count(temp_f) AS n_hours
            FROM forecast_hourly
            WHERE station_id = %(station)s AND lead_day = %(lead)s
              AND valid_time >= %(day_start)s AND valid_time < %(day_end)s
            GROUP BY model
            """,
            {"station": station, "lead": lead, "day_start": day_start, "day_end": day_end},
        )
        model_highs = [Decimal(high) for _, high, n_hours in cur.fetchall() if n_hours == 24]
        info["n_models"] = len(model_highs)
        if len(model_highs) < params["min_models"]:
            info["skipped"] = "too_few_models"
            return [], info

        mean = sum(model_highs) / len(model_highs)
        info["forecast_high"] = mean
        rounded = int(mean.quantize(Decimal("1"), rounding=ROUND_HALF_UP))

        cur.execute(
            """
            SELECT market_ticker
            FROM market_bracket
            WHERE event_ticker = %s
              AND (temp_lo IS NULL OR temp_lo <= %s)
              AND (temp_hi IS NULL OR %s <= temp_hi)
            """,
            (event_ticker, rounded, rounded),
        )
        brackets = cur.fetchall()
    if len(brackets) == 0:
        info["skipped"] = "no_bracket"
        return [], info
    if len(brackets) > 1:
        info["skipped"] = "ambiguous_bracket"
        return [], info

    chosen = brackets[0][0]
    info["chosen_market"] = chosen
    intent = {
        "decided_at": decided_at,
        "market_ticker": chosen,
        "side": "yes",
        "action": "buy",
        "quantity": params["quantity"],
        "reason": f"forecast {mean:.1f}F from {len(model_highs)} models",
    }
    return [intent], info


def intents_for_series(conn, series_ticker, start, end, params):
    """Intents and info dicts for every event of the series with start <= event_date <= end."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT ticker FROM event WHERE series_ticker = %s AND event_date BETWEEN %s AND %s "
            "ORDER BY event_date",
            (series_ticker, start, end),
        )
        event_tickers = [row[0] for row in cur.fetchall()]

    all_intents = []
    all_info = []
    for event_ticker in event_tickers:
        intents, info = intents_for_event(conn, event_ticker, params)
        all_intents.extend(intents)
        all_info.append(info)
    return all_intents, all_info


def write_csv(path, intents):
    with open(path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        writer.writeheader()
        for intent in intents:
            writer.writerow({**intent, "decided_at": intent["decided_at"].isoformat()})


def main():
    parser = argparse.ArgumentParser(description="Write the forecast-favourite order intents as CSV.")
    parser.add_argument("--series", required=True)
    parser.add_argument("--start", required=True, type=date.fromisoformat)
    parser.add_argument("--end", required=True, type=date.fromisoformat)
    parser.add_argument("--out", required=True)
    parser.add_argument("--db", default="kalbt")
    parser.add_argument("--quantity", type=int, default=DEFAULTS["quantity"])
    args = parser.parse_args()

    params = {**DEFAULTS, "quantity": args.quantity}
    with connect(args.db) as conn:
        intents, infos = intents_for_series(conn, args.series, args.start, args.end, params)

    write_csv(args.out, intents)
    for info in infos:
        if info["skipped"]:
            result = f"skipped: {info['skipped']}"
        else:
            result = info["chosen_market"]
        high = "-" if info["forecast_high"] is None else f"{info['forecast_high']:.1f}"
        print(f"{info['event_ticker']}  models {info['n_models']}  high {high}  {result}")
    print(f"{len(intents)} intents written to {args.out}")


if __name__ == "__main__":
    main()
