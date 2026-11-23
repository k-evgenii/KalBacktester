# Order-placement complexity analysis



## The two paths, step by step

### V2 

```
strategy.on_event(EventState)
  → builds OrderIntent list
  → decision_builder.build_decision()
      → validate_intents()                    O(I) + O(I) dup-scan
      → intent_to_z()  → z ∈ R^(4n)           allocate 4n vector
      → re-derive should_trade from z
  → returns StrategyDecision(z, intents, …)
engine.run_event() per bar, 15 steps:
  1  state consistency        rebuild ticker list, compare           O(n)
  2  hard gate                                                       O(1)
  3  call strategy                                                   —
  4  validate_strategy_decision  shape, finite, >=0, integer, …      O(n)
  5  log decision               build market_vals dict               O(n)
  6  no-trade check                                                  O(1)
  7  z_to_legs()  decode 4n vector back into legs                   O(4n)
  7b bin gate per leg                                                O(L)
  8  match_legs_to_state  build {ticker→ContractState}              O(n)
  9  market quality per leg                                          O(L)
  10 exit/inventory filter                                          O(L)
  11 compute_fill per leg (+ MM haircut)                            O(L)
  12 compute_fee per leg                                            O(L)
  13 copy.deepcopy(portfolio) → execute_trade_batch → commit        O(n)+deepcopy
  14 TradeRecord with FilledLeg list, z.tolist()                    O(L)
```

Round-trip per decision: `intents → z (4n) → decode back to legs`. You build a
dense 4n-wide action vector, validate it, then immediately decode it back into
the same legs you started with. For a one-leg fade that is a 4n allocation, a
shape check, and a decode — to move one order.

The `copy.deepcopy(portfolio)` at step 13 (for atomic all-or-nothing commit) is
the single most expensive op in the loop, and it runs on every executed bar.

### Cleaned version

```
strategy.on_event(EventState) → list[OrderIntent]
engine, per intent:
  gate check        is_locked()                                     O(1)
  fill              compute_fill()                                  O(1)
  fee               fee_for()                                       O(1)
  ledger.apply      dict update                                     O(1)
  record Trade                                                      O(1)
```

Round-trip per decision: `intent → fill → ledger`. No vector, no decode, no
deepcopy.

## Verdict — was it over-complicated?

**Yes, for single-market orders — but not arbitrarily.** The `z ∈ R^(4n)`
encoding is the *right* representation for a portfolio optimizer that chooses a
whole vector of legs at once and values it against payoff matrices. The mistake
in V2 was making **every** order — including a one-leg fade — pay the vector
tax: allocate 4n, validate 4n, decode 4n, deepcopy the portfolio for atomicity
that a single leg never needs.

Concretely, what was genuinely excess on the simple path:

1. **`intents → z → legs` round trip.** Encoding to a dense vector only to decode
   it back. Net information change: zero. Cut.
2. **`copy.deepcopy(portfolio)` per executed bar.** Atomicity matters for a
   multi-leg basket (all legs fill or none). For independent single legs it is
   pure cost. Cut from the core; reintroduce inside the optimizer where baskets
   are actually atomic.
3. **Two validation passes** (`validate_intents` in the builder, then
   `validate_strategy_decision` in the engine) checking overlapping invariants
   (shape, non-negativity, integer quantities). One pass is enough.
4. **Per-leg gates that can't fire** for a one-leg order (bin gate, exit filter,
   quality filter all iterate a leg list of length 1).

What was **not** over-complicated and should stay (in `statespace/`):

- The payoff matrices (`A_yes`, `A_no`, `U`) — you need them to value a basket
  across outcome states.
- Quote-ladder inversion → market-implied `q` — the optimizer's core input.
- The `z` vector itself — as the optimizer's internal language, not the
  strategy-facing interface.

## Anything missing / under-complicated?

A few gaps worth flagging, independent of the simplification:

- **No partial-fill / size model.** Both versions assume the full quantity fills
  at the touch. With `yes_bid_size_fp` / `yes_ask_size_fp` available in the
  market data, you could cap fills at displayed size. Currently unmodelled in
  both. (Deliberately left out of the cleaned core; add as a fill refinement.)
- **No position-aware sizing.** The simple strategy trades a fixed quantity; it
  never reads current exposure. Fine for the baseline, but the optimizer will
  need `theta` (current position) threaded into `EventState` — the opt-in slot
  is there for it.
- **Fee formula is the generic one.** Verify against Kalshi's current schedule;
  maker/taker and per-contract minimums can differ.
- **Settlement trusts Kalshi `result`.** Your V2 had an NWS cross-check
  (`SettlementAudit`). That is a genuinely useful integrity check worth keeping
  as an optional audit pass — it caught the case where Kalshi and NWS disagree.

## One-line summary

The order path wasn't *wrong* — it was a portfolio-optimizer's order path
charged to every single-leg trade. Moving the vector/atomicity machinery behind
`requires = {"market_state"}` makes the simple path O(1) per order while leaving
the optimizer's representation fully intact.
