"""
contracts.py — ticker parsing and outcome resolution.

Single responsibility: turn raw Kalshi market dicts into MarketSpec, and
answer "given an observed temperature, is this contract decided yet, and
which way?". No I/O.
"""
from __future__ import annotations

from datetime import datetime, timezone

from .schemas import MarketSpec

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


def spec_from_market_dict(m: dict) -> MarketSpec:
    """Build a MarketSpec from one Kalshi market record."""
    strike_type = m.get("strike_type", "greater")
    floor = m.get("floor_strike")
    cap   = m.get("cap_strike")
    floor = float(floor) if floor is not None else None
    cap   = float(cap) if cap is not None else None

    # threshold = the number that resolves the contract
    if strike_type == "greater":
        threshold = floor if floor is not None else 0.0
    elif strike_type == "less":
        threshold = cap if cap is not None else 0.0
    else:  # between
        threshold = floor if floor is not None else 0.0

    result = (m.get("result") or "").strip().lower() or None

    return MarketSpec(
        ticker       = m["ticker"],
        event_ticker = m.get("event_ticker", ""),
        strike_type  = strike_type,
        threshold    = float(threshold),
        floor        = floor,
        cap          = cap,
        close_time   = _parse_dt(m.get("close_time")),
        result       = result,
        subtitle     = m.get("subtitle"),
    )


def outcome_locked(spec: MarketSpec, observed_max_f: float) -> str | None:
    """
    Given the running-max observed temperature, return the contract's locked
    outcome ("yes"/"no") if it is already decided, else None.

    This is the engine's no-lookahead gate: it uses only temperature observed
    up to the current bar, never the final daily max.

    greater (YES if high > floor):
        observed_max > floor          → locked YES
        (cannot lock NO from below until the day ends; high can still rise)
    less    (YES if high <= cap):
        observed_max > cap            → locked NO
    between (YES if floor <= high <= cap):
        observed_max > cap            → locked NO (passed above the bin)
        floor <= observed_max <= cap  → not locked (could still rise out)
    """
    t = observed_max_f
    if spec.strike_type == "greater":
        if spec.floor is not None and t > spec.floor:
            return "yes"
        return None
    if spec.strike_type == "less":
        if spec.cap is not None and t > spec.cap:
            return "no"
        return None
    # between
    if spec.cap is not None and t > spec.cap:
        return "no"
    return None
