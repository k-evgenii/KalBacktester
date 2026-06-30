"""
builder.py — turn an EventState into a market-implied state distribution.

This is the lazy entry point the engine calls when a strategy requires
"market_state". It inverts the YES-ask ladder into disjoint bin probabilities
using pricing.implied_state_probs_from_less_yes_prices.

Returned object (MarketState) is intentionally small; extend it as the
optimizer grows (add theta, belief Pi, payoff matrices, etc.).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from .pricing import implied_state_probs_from_less_yes_prices

Array = npt.NDArray[np.float64]


@dataclass
class MarketState:
    tickers:  list[str]          # threshold-sorted
    p_lower:  Array              # monotone lower-tail YES ladder used
    q_state:  Array | None       # implied disjoint state probs (len = n+1)
    valid:    bool
    n_repaired: int = 0


def _lower_tail_yes(spec_strike_type: str, yes_ask: float) -> float:
    # greater (YES if temp>K): lower-tail = 1 - yes_ask
    # less    (YES if temp<=K): already lower-tail
    if spec_strike_type == "greater":
        return 1.0 - yes_ask
    return yes_ask


def build_market_state(state) -> MarketState:
    """state: kbt.schemas.EventState"""
    tickers: list[str] = []
    p_lower: list[float] = []

    for c in state.contracts:
        ask = c.bar.yes_ask_close
        if ask is None:
            return MarketState([c.spec.ticker for c in state.contracts],
                               np.array([]), None, valid=False)
        tickers.append(c.spec.ticker)
        p_lower.append(_lower_tail_yes(c.spec.strike_type, float(ask)))

    arr = np.asarray(p_lower, dtype=np.float64)

    # monotone (running-max) repair so inversion yields valid probabilities
    repaired = np.maximum.accumulate(arr)
    n_repaired = int(np.sum(repaired > arr + 1e-12))
    arr = np.clip(repaired, 0.0, 1.0)

    try:
        q = implied_state_probs_from_less_yes_prices(arr)
        valid = True
    except ValueError:
        q, valid = None, False

    return MarketState(tickers=tickers, p_lower=arr, q_state=q,
                       valid=valid, n_repaired=n_repaired)
