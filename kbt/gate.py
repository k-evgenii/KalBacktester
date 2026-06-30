"""
gate.py — build the per-event temperature gate from NWS observations.

The rule you described: once the day's high temperature has effectively
resolved a contract, you can no longer enter it (the book goes one-sided and
fills stop). We model that per contract: a contract is `locked_from` the first
bar-hour at which the running-max observed temperature already decides it.

This uses only observations up to each hour — never the final daily max — so
it is a valid no-lookahead gate.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from .contracts import outcome_locked
from .schemas import DayGate, MarketSpec

UTC = timezone.utc


def _floor_hour(dt: datetime) -> datetime:
    return dt.astimezone(UTC).replace(minute=0, second=0, microsecond=0)


def build_gate(
    city_key:         str,
    event_date_local: date,
    specs:            list[MarketSpec],
    observations_f:   list[tuple[datetime, float]],
) -> DayGate:
    """
    specs           : the contracts for this event-day
    observations_f  : [(ts_utc, temp_F)] for the day, time-sorted
    """
    locked_from: dict[str, datetime] = {}
    observed_max = None

    if observations_f:
        observed_max = max(t for _, t in observations_f)

    # Walk observations, tracking running max, and record the first bar-hour
    # at which each contract becomes decided.
    running_max = -1e9
    for ts, temp_f in observations_f:
        running_max = max(running_max, temp_f)
        bar_hour = _floor_hour(ts) + timedelta(hours=1)  # decided as of next bar close
        for spec in specs:
            if spec.ticker in locked_from:
                continue
            if outcome_locked(spec, running_max) is not None:
                locked_from[spec.ticker] = bar_hour

    return DayGate(
        city_key         = city_key,
        event_date_local = event_date_local,
        observed_max_f   = observed_max,
        locked_from      = locked_from,
    )


def is_locked(gate: DayGate, ticker: str, bar_ts: datetime) -> bool:
    """True if `ticker` is un-enterable at or after bar_ts."""
    lf = gate.locked_from.get(ticker)
    return lf is not None and bar_ts >= lf


def build_gate_from_max(
    city_key:         str,
    event_date_local: date,
    specs:            list[MarketSpec],
    daily_max,                      # kbt.weather.DailyMax
    blanket_time_stop: bool = True,
) -> DayGate:
    """
    Build the gate from the official CLI daily MAX (value-first, fail-closed).

    - status BLOCKED -> gate.blocked=True; the engine takes NO entries that day.
    - status OK      -> observed_max_f = max_f, plus an intraday time lock:

      blanket_time_stop=True (DEFAULT — the hard rule):
        No entry is allowed once the daily high has printed. EVERY contract is
        locked from the candle that CONTAINS the max and every candle after it,
        so the backtester cannot place a trade in the max's own hour-bar or
        later. This needs a trustworthy max time; if time_confidence == 'none'
        (we don't know WHEN the high printed) the rule can't be enforced, so the
        whole day is BLOCKED (fail-closed) rather than risk a post-max fill.

      blanket_time_stop=False (diagnostic / legacy):
        Only contracts whose outcome the max has provably decided are locked
        (outcome_locked), from the max's bar-hour. Brackets above the max stay
        enterable until close — i.e. post-max fills are possible. Kept for A/B
        comparison against the hard rule.

    The lock bar is floor(max_ts)+1h: a max at 18:24Z locks from the 19:00Z bar
    (the candle covering 18:00-19:00, which contains the max) onward; the 18:00Z
    bar (covering 17:00-18:00, before the max) is still tradeable.
    """
    if daily_max is None or daily_max.status != "OK":
        reason = getattr(daily_max, "reason", None) if daily_max is not None else "no weather record"
        return DayGate(
            city_key=city_key, event_date_local=event_date_local,
            observed_max_f=None, blocked=True, status="BLOCKED",
            block_reason=reason,
            source_id=getattr(daily_max, "source_id", None) if daily_max else None,
        )

    observed_max = float(daily_max.max_f)
    has_time = bool(daily_max.max_time_utc) and daily_max.time_confidence != "none"

    # Hard rule with no usable max time -> cannot prove a fill was pre-max -> block.
    if blanket_time_stop and not has_time:
        return DayGate(
            city_key=city_key, event_date_local=event_date_local,
            observed_max_f=observed_max, blocked=True, status="BLOCKED",
            block_reason="blanket_time_stop: max time unknown (time_confidence=none)",
            max_time_confidence=daily_max.time_confidence,
            source_id=daily_max.source_id,
        )

    locked_from: dict[str, datetime] = {}
    if has_time:
        max_ts = datetime.fromisoformat(daily_max.max_time_utc)
        lock_bar = _floor_hour(max_ts) + timedelta(hours=1)
        if blanket_time_stop:
            # lock EVERY contract from the max's own candle onward
            for spec in specs:
                locked_from[spec.ticker] = lock_bar
        else:
            # legacy: lock only contracts the max has decided
            for spec in specs:
                if outcome_locked(spec, observed_max) is not None:
                    locked_from[spec.ticker] = lock_bar

    return DayGate(
        city_key=city_key, event_date_local=event_date_local,
        observed_max_f=observed_max, locked_from=locked_from,
        blocked=False, status="OK",
        max_time_confidence=daily_max.time_confidence,
        source_id=daily_max.source_id,
    )