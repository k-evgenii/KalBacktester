"""
statespace — OPT-IN portfolio-optimization machinery.

This subpackage is NOT touched by simple strategies. It is built lazily by the
engine only when a strategy declares `requires = {"market_state"}`. It holds
the pieces your future multi-market optimizer needs:

    market.py    payoff geometry (latent states, YES/NO payoff matrices, U)
    pricing.py   quote inversion + model-implied prices (Pi vs market q)
    builder.py   EventState -> market_state (quote-ladder inversion)

When you build the optimizer, this is where the z-vector, theta exposure, and
belief providers live — kept out of the core path so the simple backtester
stays legible.
"""
