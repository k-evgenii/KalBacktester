"""
fetch_june.py — fetch Kalshi + NWS weather data for June 2026.

Zero dependencies outside stdlib. Writes data in the exact layout kbt expects:

    data/raw/kalshi/{city_key}/markets/{series}.json
    data/raw/kalshi/{city_key}/candles/{ticker}.json
    data/raw/nws/{city_key}/observations/{station}_{YYYY-MM-DD}.json

Usage:
    python fetch_june.py                        # writes to ./data/
    python fetch_june.py --out /path/to/data    # custom root
    python fetch_june.py --dry-run              # show what would be fetched, no API calls

Cities fetched:
    new_york    KXHIGHNY     KNYC
    los_angeles KXHIGHLAX    KLAX
    miami       KXHIGHMIA    KMIA

NOTE on series tickers:
    Verify KXHIGHLAX and KXHIGHMIA on https://kalshi.com/browse before running —
    Kalshi occasionally changes series names. If you get 0 markets for a city,
    that is the first thing to check.
"""
from __future__ import annotations

import argparse
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger("fetch")
logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")

UTC = timezone.utc

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

KALSHI_BASE = "https://api.elections.kalshi.com/trade-api/v2"
NWS_BASE    = "https://api.weather.gov"

# Must supply a user-agent for NWS or it returns 403.
NWS_UA = "kbt-fetcher/3.0 (https://github.com/yourrepo; your@email.com)"

# June 2026 window
START_DATE = date(2026, 6, 1)
END_DATE   = date(2026, 6, 30)   # inclusive

CITIES: list[dict] = [
    {"key": "new_york",     "series": "KXHIGHNY",    "station": "KNYC"},
    {"key": "los_angeles",  "series": "KXHIGHLAX",   "station": "KLAX"},
    {"key": "miami",        "series": "KXHIGHMIA",   "station": "KMIA"},
]

RATE_KALSHI = 0.12   # seconds between Kalshi requests  (~8 req/s, well under 30/s)
RATE_NWS    = 0.25   # NWS is more conservative


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

def _get(url: str, params: dict | None = None, headers: dict | None = None,
         timeout: int = 20, retry: int = 3) -> dict:
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        url += "?" + urllib.parse.urlencode(clean)
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    for attempt in range(retry):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            if e.code == 429:
                wait = 5 * (attempt + 1)
                logger.warning("429 rate-limit on %s — sleeping %ds", url.split("?")[0], wait)
                time.sleep(wait)
            elif e.code == 404:
                return {}
            else:
                raise
        except Exception as e:
            if attempt == retry - 1:
                raise
            logger.warning("request failed (%s), retry %d/%d", e, attempt + 1, retry)
            time.sleep(2)
    return {}


# ---------------------------------------------------------------------------
# Kalshi
# ---------------------------------------------------------------------------

def kalshi_get_cutoff() -> datetime | None:
    """Returns the timestamp before which markets are on the historical surface."""
    try:
        resp = _get(f"{KALSHI_BASE}/historical/cutoff")
        ts = resp.get("market_settled_ts")
        if ts:
            return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception as e:
        logger.warning("could not fetch cutoff: %s — defaulting to live surface", e)
    return None


def kalshi_fetch_markets(series: str) -> list[dict]:
    """
    Fetch all markets for a series from the live /markets endpoint (paginated).
    Falls back per event-day to /historical/markets for any June dates not found.
    """
    markets: dict[str, dict] = {}    # ticker → market dict
    cursor: str | None = None
    page = 0

    logger.info("  fetching markets for %s …", series)
    while True:
        params: dict = {"series_ticker": series, "limit": 200}
        if cursor:
            params["cursor"] = cursor
        try:
            resp = _get(f"{KALSHI_BASE}/markets", params)
        except Exception as e:
            logger.error("  /markets failed: %s", e)
            break
        time.sleep(RATE_KALSHI)

        batch = resp.get("markets", [])
        for m in batch:
            markets[m["ticker"]] = m
        cursor = resp.get("cursor")
        page += 1
        logger.info("  page %d: %d total markets so far", page, len(markets))
        if not cursor or not batch:
            break

    # Check how many June markets we have already
    june_events = _june_event_tickers(series)
    missing_events = [et for et in june_events if not any(
        m.get("event_ticker") == et for m in markets.values()
    )]

    if missing_events:
        logger.info("  %d June event-days missing from live surface, trying historical …",
                    len(missing_events))
        for et in missing_events:
            try:
                resp = _get(f"{KALSHI_BASE}/historical/markets", {"event_ticker": et})
                time.sleep(RATE_KALSHI)
                for m in resp.get("markets", []):
                    markets[m["ticker"]] = m
            except Exception as e:
                logger.warning("  /historical/markets %s failed: %s", et, e)

    result = list(markets.values())
    # filter to June 2026
    result = [m for m in result if _is_june(m)]
    logger.info("  %d June markets found for %s", len(result), series)
    return result


def _is_june(m: dict) -> bool:
    et = m.get("event_ticker", "")
    return "26JUN" in et


def _june_event_tickers(series: str) -> list[str]:
    """Generate all expected event-tickers for June 2026."""
    out = []
    d = START_DATE
    while d <= END_DATE:
        day = f"{d.day:02d}"
        mon = d.strftime("%b").upper()
        out.append(f"{series}-26{mon}{day}")
        d += timedelta(days=1)
    return out


def _parse_candle(c: dict, ticker: str, city_key: str, open_time: str | None,
                  close_time: str | None, source: str) -> dict | None:
    """
    Convert one raw Kalshi candlestick API dict into the bar format the engine reads.
    Handles both the live surface (_dollars suffix) and historical surface (plain keys).
    """
    raw_ts = c.get("end_period_ts")
    if raw_ts is None:
        return None
    if isinstance(raw_ts, (int, float)):
        ts_str = datetime.fromtimestamp(raw_ts, tz=UTC).isoformat()
    else:
        ts_str = str(raw_ts)

    def _f(obj: dict | None, key: str) -> float | None:
        if not obj:
            return None
        v = obj.get(f"{key}_dollars") or obj.get(key)
        return float(v) if v is not None else None

    pr = c.get("price") or {}
    yb = c.get("yes_bid") or {}
    ya = c.get("yes_ask") or {}

    vol = c.get("volume_fp") if c.get("volume_fp") is not None else c.get("volume")
    oi  = c.get("open_interest_fp") if c.get("open_interest_fp") is not None else c.get("open_interest")

    return {
        "ticker":           ticker,
        "end_period_ts":    ts_str,
        "open_time":        open_time,
        "close_time":       close_time,
        "volume_fp":        float(vol) if vol is not None else 0.0,
        "open_interest_fp": float(oi) if oi is not None else 0.0,
        "price_open":       _f(pr, "open"),
        "price_high":       _f(pr, "high"),
        "price_low":        _f(pr, "low"),
        "price_close":      _f(pr, "close"),
        "price_mean":       _f(pr, "mean"),
        "yes_bid_open":     _f(yb, "open"),
        "yes_bid_high":     _f(yb, "high"),
        "yes_bid_low":      _f(yb, "low"),
        "yes_bid_close":    _f(yb, "close"),
        "yes_ask_open":     _f(ya, "open"),
        "yes_ask_high":     _f(ya, "high"),
        "yes_ask_low":      _f(ya, "low"),
        "yes_ask_close":    _f(ya, "close"),
        "city_key":         city_key,
        "raw_source":       source,
        "extra":            {},
    }


def kalshi_fetch_candles(ticker: str, series: str, city_key: str,
                         open_time: str | None, close_time: str | None,
                         cutoff: datetime | None,
                         start_ts: int, end_ts: int) -> list[dict]:
    """
    Try live surface, fall back to historical. Return list of bar dicts.
    """
    def _live() -> list[dict] | None:
        try:
            resp = _get(
                f"{KALSHI_BASE}/series/{series}/markets/{ticker}/candlesticks",
                {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 60},
            )
            time.sleep(RATE_KALSHI)
            return resp.get("candlesticks") or []
        except Exception as e:
            logger.debug("live candles failed for %s: %s", ticker, e)
            return None

    def _hist() -> list[dict] | None:
        try:
            resp = _get(
                f"{KALSHI_BASE}/historical/markets/{ticker}/candlesticks",
                {"start_ts": start_ts, "end_ts": end_ts, "period_interval": 60},
            )
            time.sleep(RATE_KALSHI)
            return resp.get("candlesticks") or []
        except Exception as e:
            logger.debug("historical candles failed for %s: %s", ticker, e)
            return None

    # Determine primary surface
    use_hist = cutoff is not None and close_time is not None and (
        datetime.fromisoformat(close_time.replace("Z", "+00:00")) < cutoff
    )

    if use_hist:
        raw = _hist() or _live() or []
        source = "historical"
    else:
        raw = _live() or _hist() or []
        source = "live"

    out = []
    for c in raw:
        bar = _parse_candle(c, ticker, city_key, open_time, close_time, source)
        if bar:
            out.append(bar)
    return out


# ---------------------------------------------------------------------------
# NWS
# ---------------------------------------------------------------------------

def nws_fetch_observations(station: str, day: date) -> dict:
    """
    Fetch all NWS observations for one station/day. Returns the GeoJSON dict
    ready to save (same format as the existing obs files the engine reads).
    """
    start = datetime(day.year, day.month, day.day, 0, 0, 0, tzinfo=UTC)
    end   = start + timedelta(days=1)
    params = {
        "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end":   end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "limit": 200,
    }
    headers = {"User-Agent": NWS_UA}

    all_features: list[dict] = []
    url: str | None = f"{NWS_BASE}/stations/{station}/observations"
    p: dict | None = params

    while url:
        try:
            resp = _get(url, params=p, headers=headers)
            time.sleep(RATE_NWS)
        except Exception as e:
            logger.warning("    NWS obs %s %s failed: %s", station, day, e)
            break
        all_features.extend(resp.get("features") or [])
        pag = resp.get("pagination") or {}
        url = pag.get("next") if isinstance(pag, dict) else None
        p = None   # next_url already has params baked in

    return {"type": "FeatureCollection", "features": all_features}


# ---------------------------------------------------------------------------
# Save helpers
# ---------------------------------------------------------------------------

def _save(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, default=str))
    logger.debug("saved %s", path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def fetch_city(city: dict, data_root: Path, cutoff: datetime | None, dry_run: bool) -> None:
    key     = city["key"]
    series  = city["series"]
    station = city["station"]

    logger.info("=" * 56)
    logger.info("CITY: %s  series=%s  station=%s", key, series, station)
    logger.info("=" * 56)

    # ---- 1. Markets --------------------------------------------------------
    markets_path = data_root / "raw" / "kalshi" / key / "markets" / f"{series}.json"
    if markets_path.exists():
        existing = json.loads(markets_path.read_text())
        logger.info("markets file exists (%d). Merging with fresh fetch.", len(existing))
    else:
        existing = []

    if dry_run:
        logger.info("[DRY RUN] would fetch markets for %s", series)
        markets = existing or [{"ticker": f"{series}-26JUN01-T80", "event_ticker": f"{series}-26JUN01",
                                "strike_type": "greater", "floor_strike": 80, "result": "yes",
                                "close_time": "2026-06-02T04:59:00Z"}]
    else:
        fresh = kalshi_fetch_markets(series)
        # merge: fresh takes priority, keep existing not in fresh
        existing_tickers = {m["ticker"] for m in fresh}
        merged = fresh + [m for m in existing if m["ticker"] not in existing_tickers]
        markets = merged
        _save(markets_path, markets)
        logger.info("saved %d markets → %s", len(markets), markets_path)

    if not markets:
        logger.warning("No markets found for %s. Check the series ticker.", series)
        return

    # ---- 2. Candles --------------------------------------------------------
    candles_dir = data_root / "raw" / "kalshi" / key / "candles"
    for m in markets:
        ticker     = m.get("ticker", "")
        open_time  = m.get("open_time")
        close_time = m.get("close_time")

        if not ticker:
            continue

        # Time window: from open_time to close_time (or ±2 days around the event)
        try:
            if open_time:
                t0 = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
            else:
                t0 = datetime(2026, 6, 1, tzinfo=UTC)
            if close_time:
                t1 = datetime.fromisoformat(close_time.replace("Z", "+00:00"))
            else:
                t1 = t0 + timedelta(days=3)
        except Exception:
            t0 = datetime(2026, 6, 1, tzinfo=UTC)
            t1 = t0 + timedelta(days=3)

        candle_path = candles_dir / f"{ticker}.json"

        if dry_run:
            logger.info("[DRY RUN] would fetch candles for %s", ticker)
            continue

        if candle_path.exists():
            logger.debug("candles already cached: %s", ticker)
            continue

        bars = kalshi_fetch_candles(
            ticker, series, key, open_time, close_time,
            cutoff, int(t0.timestamp()), int(t1.timestamp()),
        )
        _save(candle_path, bars)
        logger.info("  %s → %d bars", ticker, len(bars))

    # ---- 3. NWS observations -----------------------------------------------
    obs_dir = data_root / "raw" / "nws" / key / "observations"
    d = START_DATE
    while d <= END_DATE:
        obs_path = obs_dir / f"{station}_{d.isoformat()}.json"
        if obs_path.exists():
            logger.debug("obs cached: %s %s", station, d)
            d += timedelta(days=1)
            continue

        if dry_run:
            logger.info("[DRY RUN] would fetch NWS obs %s %s", station, d)
        else:
            logger.info("  NWS obs %s %s …", station, d)
            obs = nws_fetch_observations(station, d)
            _save(obs_path, obs)
            n = len(obs.get("features", []))
            logger.info("  saved %d obs features → %s", n, obs_path.name)
        d += timedelta(days=1)


def main() -> None:
    ap = argparse.ArgumentParser(description="Fetch Kalshi + NWS data for June 2026")
    ap.add_argument("--out", type=Path, default=Path("data"), help="data root dir (default: ./data)")
    ap.add_argument("--city", choices=["new_york", "los_angeles", "miami", "all"], default="all")
    ap.add_argument("--dry-run", action="store_true", help="print what would be fetched, no API calls")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if args.debug:
        logging.getLogger().setLevel(logging.DEBUG)

    if not args.dry_run:
        logger.info("Fetching historical cutoff …")
        cutoff = kalshi_get_cutoff()
        logger.info("Cutoff: %s", cutoff)
    else:
        cutoff = None
        logger.info("[DRY RUN] skipping cutoff fetch")

    cities = CITIES if args.city == "all" else [c for c in CITIES if c["key"] == args.city]

    for city in cities:
        fetch_city(city, args.out, cutoff, dry_run=args.dry_run)

    logger.info("")
    logger.info("Done. Data at: %s", args.out.resolve())
    logger.info("")
    logger.info("Run the engine:")
    for city in cities:
        logger.info("  python -m kbt.run --data %s --city %s --series %s --out ./out/%s",
                    args.out, city["key"], city["series"], city["key"])


if __name__ == "__main__":
    main()