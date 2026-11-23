"""
kbt — Kalshi weather backtester (cleaned).

Tiers:
  CORE        loaders, gate, fills, ledger, engine, metrics  (always on)
  STRATEGY    Strategy base + OrderIntent (the universal output)
  STATESPACE  opt-in portfolio-optimization machinery (payoff geometry,
              quote inversion) — only built when a strategy declares it
              via `requires = {"market_state"}`.

A strategy returns list[OrderIntent]. Nothing else. The engine handles
gating, fills, fees, the ledger, settlement, and metrics.
"""
from __future__ import annotations

__version__ = "3.0.0"
