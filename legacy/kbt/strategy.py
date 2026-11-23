"""
strategy.py — the strategy contract.

A strategy implements one method:

    on_event(state: EventState) -> list[OrderIntent]

That is the entire interface. No z-vectors, no fills, no fees, no portfolio.
Return an empty list to hold.

Opt-in machinery
----------------
A strategy may declare what extra, expensive inputs it needs:

    requires = {"market_state"}

When present, the engine lazily builds state.market_state (quote-ladder
inversion → market-implied state distribution) before calling on_event.
Simple strategies declare nothing and never pay that cost — this is the
"pay for what you use" boundary between the simple path and the optimizer.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .schemas import EventState, OrderIntent


@runtime_checkable
class Strategy(Protocol):
    name: str
    params: dict
    requires: set[str]   # subset of {"market_state"}; usually empty

    def on_event(self, state: EventState) -> list[OrderIntent]:
        ...


class BaseStrategy:
    """Convenience base so strategies don't repeat boilerplate."""
    name: str = "BaseStrategy"
    requires: set[str] = set()

    @property
    def params(self) -> dict:
        return {}

    def on_event(self, state: EventState) -> list[OrderIntent]:
        raise NotImplementedError
