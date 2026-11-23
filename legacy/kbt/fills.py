"""
fills.py — fill price and fee for one leg.

Fill convention (you cross the book; MM presence makes this immediate):
    buy_yes  → pay     yes_ask
    sell_yes → receive yes_bid
    buy_no   → pay     1 - yes_bid     (NO ask = 1 - YES bid)
    sell_no  → receive 1 - yes_ask     (NO bid = 1 - YES ask)

An optional symmetric `slippage` (default 0) widens the crossing price; set it
if you later want to model MM adverse selection. The V2 confidence-scaled
haircut is intentionally not reintroduced here — add it back as a slippage
function if/when you have evidence for its size.

FEE: approximation of Kalshi's published general formula
        fee = ceil(0.07 * C * P * (1 - P))   [dollars, rounded up to the cent]
Verify against Kalshi's current fee schedule before trusting absolute PnL —
this is the standard formula, not a guaranteed match to today's tiers.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from .schemas import Action, Bar

BUY = {"buy_yes", "buy_no"}


@dataclass(frozen=True)
class FillResult:
    ok:         bool
    fill_price: float
    reason:     str = ""


def fee_for(quantity: int, price: float, fee_rate: float = 0.07) -> float:
    """Kalshi-style fee in dollars, rounded up to the next cent."""
    raw = fee_rate * quantity * price * (1.0 - price)
    return math.ceil(raw * 100.0) / 100.0


def compute_fill(bar: Bar, action: Action, slippage: float = 0.0) -> FillResult:
    bid, ask = bar.yes_bid_close, bar.yes_ask_close
    if bid is None or ask is None:
        return FillResult(False, 0.0, "missing bid/ask")
    if bid > ask:
        return FillResult(False, 0.0, "crossed quotes")

    if action == "buy_yes":
        price = ask + slippage
    elif action == "sell_yes":
        price = bid - slippage
    elif action == "buy_no":
        price = (1.0 - bid) + slippage
    elif action == "sell_no":
        price = (1.0 - ask) - slippage
    else:
        return FillResult(False, 0.0, f"bad action {action!r}")

    price = min(1.0, max(0.0, price))
    return FillResult(True, price)
