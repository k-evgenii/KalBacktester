"""
loaders.py — read the raw data layout into typed objects.

Expected layout (matches the existing repo):

  data/raw/kalshi/<city>/markets/<SERIES>.json     list[ market dict ]
  data/raw/kalshi/<city>/candles/<TICKER>.json      list[ bar dict ]
  data/raw/nws/<city>/observations/<STN>_<date>.json GeoJSON FeatureCollection

NWS temperatures are Celsius → converted to Fahrenheit here (thresholds are °F).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .schemas import Bar

UTC = timezone.utc


def _parse_dt(s: str | None) -> datetime | None:
    if not s:
        return None
    s = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def _c_to_f(c: float) -> float:
    return c * 9.0 / 5.0 + 32.0


def load_markets(markets_dir: Path, series: str) -> list[dict]:
    """Return the raw list of market dicts for a series (e.g. 'KXHIGHNY')."""
    path = markets_dir / f"{series}.json"
    if not path.exists():
        raise FileNotFoundError(f"market file not found: {path}")
    return json.loads(path.read_text())


def load_bars(candles_dir: Path, ticker: str) -> list[Bar]:
    """Load and sort one contract's hourly bars."""
    path = candles_dir / f"{ticker}.json"
    if not path.exists():
        return []
    raw = json.loads(path.read_text())
    bars: list[Bar] = []
    for b in raw:
        ts = _parse_dt(b.get("end_period_ts"))
        if ts is None:
            continue
        bars.append(Bar(
            ticker        = b.get("ticker", ticker),
            ts            = ts,
            open          = b.get("price_open"),
            high          = b.get("price_high"),
            low           = b.get("price_low"),
            close         = b.get("price_close"),
            volume        = float(b.get("volume_fp") or 0.0),
            yes_bid_close = b.get("yes_bid_close"),
            yes_ask_close = b.get("yes_ask_close"),
        ))
    bars.sort(key=lambda x: x.ts)
    return bars


def load_observations_f(obs_dir: Path, station: str, day: str) -> list[tuple[datetime, float]]:
    """
    Return [(timestamp_utc, temp_F)] for one station/day, sorted by time.
    Empty if the file is missing or has no features.
    """
    path = obs_dir / f"{station}_{day}.json"
    if not path.exists():
        return []
    doc = json.loads(path.read_text())
    out: list[tuple[datetime, float]] = []
    for feat in doc.get("features", []):
        props = feat.get("properties", {})
        ts = _parse_dt(props.get("timestamp"))
        temp = (props.get("temperature") or {}).get("value")
        if ts is None or temp is None:
            continue
        unit = (props.get("temperature") or {}).get("unitCode", "")
        temp_f = _c_to_f(float(temp)) if "degC" in unit else float(temp)
        out.append((ts, temp_f))
    out.sort(key=lambda x: x[0])
    return out


def load_weather(processed_dir: Path, city: str) -> dict:
    """
    Load the CLI daily-max cache for a city.

    File: <processed_dir>/weather/<city>.json
        { "2026-06-02": {DailyMax.as_dict()}, ... }

    Returns {iso_date: DailyMax}. Missing file → {} (every day then blocks,
    which is the safe default until the cache is fetched).
    """
    from .weather import DailyMax  # local import to avoid cycles

    path = processed_dir / "weather" / f"{city}.json"
    if not path.exists():
        return {}
    raw = json.loads(path.read_text())
    out: dict[str, DailyMax] = {}
    for iso, rec in raw.items():
        # tolerate unknown/extra keys defensively
        out[iso] = DailyMax(**{k: rec.get(k) for k in DailyMax.__dataclass_fields__})
    return out
