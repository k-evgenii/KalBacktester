"""
schemas.py — slim data containers.

Pure dataclasses. No logic, no I/O. Compared with V2 this drops the
RESERVED tick/L2 layer (RawTrade, TradeFeatures, BarQuality flags),
the theta/q_market_state fields baked into EventState (now optional and
opt-in), and the FilledLeg/z bookkeeping (the engine fills per intent).

Prices are YES prices in [0, 1]. NO price = 1 - YES price.
All datetimes are UTC-aware.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

# Action vocabulary — the only four things a strategy can ask for.
Action = Literal["buy_yes", "sell_yes", "buy_no", "sell_no"]
StrikeType = Literal["greater", "less", "between"]


# ---------------------------------------------------------------------------
# Market metadata (one binary contract)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class MarketSpec:
    ticker:       str
    event_ticker: str
    strike_type:  StrikeType        # greater | less | between
    threshold:    float             # the resolving number in °F (see note)
    floor:        float | None      # lower bound (greater/between)
    cap:          float | None      # upper bound (less/between)
    close_time:   datetime | None
    result:       str | None        # "yes" | "no" | None (unsettled)
    subtitle:     str | None = None

    # NOTE on `threshold`:
    #   greater  (T-contract): YES if observed_high  > floor   → threshold = floor
    #   less     (T-contract): YES if observed_high <= cap     → threshold = cap
    #   between  (B-contract): YES if floor <= high <= cap     → threshold = floor


# ---------------------------------------------------------------------------
# One OHLCV bar (hourly)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Bar:
    ticker:        str
    ts:            datetime          # bar close (UTC)
    open:          float | None
    high:          float | None
    low:           float | None
    close:         float | None
    volume:        float
    yes_bid_close: float | None      # best YES bid at bar close
    yes_ask_close: float | None      # best YES ask at bar close

    @property
    def mid(self) -> float | None:
        if self.yes_bid_close is None or self.yes_ask_close is None:
            return self.close
        return (self.yes_bid_close + self.yes_ask_close) / 2.0


# ---------------------------------------------------------------------------
# Per-event temperature gate
# ---------------------------------------------------------------------------

@dataclass
class DayGate:
    """
    Resolution gate for one event-day, derived from NWS observations.

    locked_from[ticker] = first bar-hour (UTC) at which that contract's
    outcome is already decided by observed temperature, so entry is
    forbidden from that hour onward. Absent ticker = never locked.
    """
    city_key:         str
    event_date_local: date
    observed_max_f:   float | None
    locked_from:      dict[str, datetime] = field(default_factory=dict)
    # weather-data safety: a BLOCKED day means the official max could not be
    # trusted (missing / typo / wrong date / wrong station) → no entries at all.
    blocked:             bool = False
    status:              str = "OK"      # "OK" | "BLOCKED"
    block_reason:        str | None = None
    max_time_confidence: str = "none"    # "clean" | "assumed" | "none"
    source_id:           str | None = None


# ---------------------------------------------------------------------------
# Strategy input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Contract:
    spec: MarketSpec
    bar:  Bar
    q_implied: float | None          # market-implied YES prob (= bar.mid)


@dataclass
class EventState:
    """What the strategy sees, one per (event, bar timestamp)."""
    city_key:         str
    event_date_local: date
    ts:               datetime
    contracts:        list[Contract]   # sorted by threshold ascending
    gate:             DayGate

    # OPT-IN — only populated when strategy.requires contains "market_state".
    # None for simple strategies (the lazy path: ladder inversion is skipped).
    market_state: Any = None


# ---------------------------------------------------------------------------
# Strategy output — the universal interface
# ---------------------------------------------------------------------------

@dataclass
class OrderIntent:
    """One desired leg. A strategy returns a list of these. That is all."""
    ticker:   str
    action:   Action
    quantity: int
    reason:   str = ""
    meta:     dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution record
# ---------------------------------------------------------------------------

@dataclass
class Trade:
    ts:               datetime
    city_key:         str
    event_date_local: date
    ticker:           str
    action:           Action
    quantity:         int
    fill_price:       float           # price actually paid/received (the traded side)
    fee:              float
    reason:           str = ""

    # filled at settlement
    result:           str | None = None    # "yes" | "no"
    pnl:              float | None = None   # net $ for this trade
