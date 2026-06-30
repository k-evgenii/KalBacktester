# kbt — Kalshi weather backtester (cleaned)

A small, legible historical backtester for Kalshi weather markets, with the
multi-market portfolio-optimization machinery kept intact but **opt-in**.

## Run

```bash
python -m kbt.run --data ./data --city new_york --series KXHIGHNY --out ./out
```

Outputs (with `--out`): `metrics.json`, `trades.csv`, `equity.csv`.

## The one rule a strategy follows

```python
def on_event(self, state: EventState) -> list[OrderIntent]:
    ...
```

Return a list of `OrderIntent(ticker, action, quantity)`. That's the whole
interface — no z-vectors, no fills, no fees, no portfolio. The engine does
gating, fills@bid/ask, fees, the ledger, settlement, and metrics.

## Tiers

```
CORE        loaders · gate · fills · ledger · engine · metrics   (always on)
STRATEGY    BaseStrategy + OrderIntent                            (the interface)
STATESPACE  payoff geometry · quote inversion                    (opt-in, lazy)
```

A strategy opts into the heavy machinery by declaring:

```python
requires = {"market_state"}
```

Only then does the engine build `state.market_state` (quote-ladder inversion →
market-implied state distribution). Simple strategies declare nothing and never
pay that cost. This is the boundary that keeps the simple path cheap while
leaving the optimizer fully wired for your future multi-market "whale".

## What changed from V2

- **Packaging fixed.** One import scheme, proper package, zero `sys.path.insert`
  hacks (was 29 files), zero `weatherman.*` imports (was a runtime `ImportError`).
- **`q_market_state` is lazy.** Was computed every bar even when unused.
- **RESERVED layer deferred.** `RawTrade`, `TradeFeatures`, L2/orderbook hooks,
  the `BarQuality` flag set — removed until tick/L2 mode actually exists.
- **`OrderIntent` is the strategy output.** The `z`-vector is now internal to the
  optimizer (`statespace/`), not the thing every strategy must emit.
- **Engine is one function.** The 15-step gauntlet (z decode, multi-leg atomic
  fills, partial-fill config, deepcopy commit) collapses for single-leg fills.

## Settlement & PnL

Settles from the Kalshi `result` field. Per-trade PnL is `qty * (payoff - price)
- fee`. Trades whose market never settled are excluded from PnL stats and
reported as `unsettled`.

## Fee note

Fee uses Kalshi's published general formula `ceil(0.07 * C * P * (1-P))` per
fill. Verify against the current Kalshi fee schedule before trusting absolute
PnL.

## Layout

```
kbt/
  schemas.py        slim dataclasses
  contracts.py      ticker parsing + outcome resolution
  loaders.py        read candle/market/obs JSON  (NWS °C → °F)
  gate.py           temperature lock per contract (no-lookahead)
  fills.py          fill@bid/ask + fee
  ledger.py         cash + positions + equity curve + settle
  engine.py         the core loop
  metrics.py        evaluation stats + report
  strategy.py       Strategy protocol + BaseStrategy
  strategies/
    highest_temp.py the simple on-market test strategy
  statespace/       opt-in optimizer machinery
    market.py       payoff geometry
    pricing.py      quote inversion / model-implied prices
    builder.py      EventState → market_state
  run.py            CLI
```
