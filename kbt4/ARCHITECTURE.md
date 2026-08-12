# kbt4 — Architecture

Companion to `KALSHI_DOMAIN.md`, which holds the market facts this is built
against. This document holds the *choices*. Update it in the same commit as the
code it describes.

## Three rules

1. **Simplicity.** Fewest moving parts that model the market honestly.
2. **Suitable algorithms and data structures** — suitable, not sophisticated. A
   rejected clever structure is recorded below with the reason.
3. **Kalshi's structure is fixed.** It lives in `KALSHI_DOMAIN.md` and is not
   redesigned to make the code nicer.

---

## Dependency rule

```
data/  ──►  core/  ◄──  strategies/
```

`core/` imports neither. If the engine ever wants a loader, the abstraction is
wrong. Everything crossing a boundary is a dataclass from `core/types.py`.

---

## Decision log

Append-only. Date, decision, why, what was rejected.

### D1 — Money is integer cents
All prices, cash, fees and PnL are `int` cents. Kalshi is natively
cent-denominated; floats in [0,1] accumulate drift in cash accounting and make
settlement comparisons fuzzy. The taker fee becomes exact integer arithmetic:

```
fee_cents = ceil( 7 × C × p × (100 − p) / 10000 )        p = YES price in cents
```

*Rejected:* floats in [0,1] (v3). *Rejected:* `Decimal` — slower, and unnecessary
once everything is integral.

### D2 — A position is one signed integer
Per market: `net` in YES units. Positive = long YES, negative = long NO, zero =
flat. Buys move away from zero, sells move toward it.

Follows directly from market structure: a Kalshi event market is one contract
with a long and a short side, and naked shorting does not exist. v3's independent
`pos.yes` / `pos.no` counters, both allowed to go negative, describe an
instrument that isn't this one.

*Rejected:* v3's two-counter model — permits impossible states.

### D3 — Both lifecycles: hold to settlement and early exit
Strategies may close before the event resolves.

### D4 — Average cost basis
Position state is `(net, avg_cost)`. On a partial close, realised PnL is
`qty × (exit_price − avg_cost)` and `avg_cost` is unchanged. On a sign flip the
old basis is fully realised and the new basis is the opening leg's price.

Lifetime PnL is identical under FIFO; only the *timing* of recognition differs,
which changes the shape of the equity curve and therefore the daily return
series. Average cost is chosen because FIFO's purpose is tax lot identification,
which does not apply here.

*Rejected:* FIFO. It needs a deque of lots per market and buys nothing we
measure. **This is a deliberate rejection of the more interesting data structure**
— see rule 2. If per-lot holding-period analysis is ever wanted, FIFO goes behind
the same interface.

### D5 — Fees are expensed at trade time
Not capitalised into cost basis. Kalshi charges them as cash out the door. A
round trip nets to `(exit − entry) × qty − entry_fee − exit_fee`, with each fee
landing on its own timestamp.

### D6 — Taker fees always
Even when a fill model other than touch is configured. Conservative, and it keeps
the fill model and fee model decoupled.

*Rejected:* maker rate at mid fills — couples two independent knobs and flatters
the backtest.

### D7 — Fills cross the touch
`buy_yes` pays `yes_ask`, `sell_yes` receives `yes_bid`, `buy_no` pays
`100 − yes_bid`, `sell_no` receives `100 − yes_ask`. Mid fills are available as a
config knob for A/B comparison; touch is the default.

### D8 — Same-bar fill at close quotes
A decision made at bar *N*'s close fills at bar *N*'s `yes_bid_close` /
`yes_ask_close`.

**This is not lookahead.** Every field of bar *N* — including high, low and
volume — is known at bar *N*'s close. The decision and the fill are the same
instant. The genuine optimism is elsewhere and is stated rather than hidden:
zero latency between decision and fill, and zero market impact.

### D9 — Fills capped at 10% of the *previous* bar's volume
Configurable participation rate, default 0.10, measured against bar *N−1*.

Bar *N*'s volume would be defensible (it is known at *N*'s close) but it is
liquidity that has already traded, used as a proxy for liquidity available at the
close. Using *N−1* is strictly conservative.

**The first bar of each event-day is untradeable** — no previous bar exists.
Consistent with the fail-closed convention, and first bars are thin anyway.

The cap is also standing in for something the data cannot show: with only
top-of-book quotes, a market order that would walk down several price levels is
invisible. The participation cap approximates that slippage.

### D10 — Orders live exactly one bar
An order is placed, fills or partially fills, and dies at the end of the bar. A
strategy that still wants the position re-issues next bar; silence is
cancellation.

This is the decision that collapses the most machinery. There is no order
lifecycle across time, no TTL, no expiry events, no cancel verb in the strategy
interface, and no cross-bar capital reservation. "Resting" becomes an emergent
behaviour of a stateless strategy rather than an engine feature — nothing to
maintain.

### D11 — Cash reserved at placement-time ask
Exact rather than approximate, because D10 makes placement and fill the same
instant at the same quote. Reservation is released at end of bar.

*Rejected:* reserving at 99¢ (absurd capital lockup); re-pricing each bar
(unnecessary once orders are bar-scoped).

### D12 — FIFO by placement time when the volume cap binds
Matches Kalshi's price-time priority. Only applies *within* a bar, among orders
issued on that bar, so the structure is a per-bar list processed in order — not a
persistent queue.

*Rejected:* pro-rata (not what Kalshi does), size priority (not what anyone does).

### D13 — Legs fill independently; no atomicity
Kalshi offers no atomic multi-leg order, so modelling one would let the backtest
do something the exchange won't.

Two consequences, both accepted:
- `PortfolioState` **must** expose current positions and the previous bar's fills,
  or the strategy is held responsible for leg risk it cannot observe.
- On hourly bars this is *conservative*, not neutral. A real trader corrects a
  half-filled spread in seconds; this one waits up to an hour. Spread strategies
  will look worse here than they would trade.

*Rejected:* basket ids (initially recommended, then withdrawn — it models an
order type Kalshi does not offer).

### D14 — Fixed bankroll, $2,000, orders rejected on insufficient cash
`2_000_00` cents. Shared across all cities; no per-city allocation.

Sizing note, not an engine constraint: fee as a fraction of capital deployed is
`0.07 × (1 − p)` — the `C × p` cancels. So per dollar risked, longshots are the
*most* expensive, the opposite shape to the per-contract fee that peaks at 50¢. A
20¢ position pays 5.6% each way; an 80¢ position pays 1.4%.

### D15 — Settlement at CLI publication on d+1
Capital is locked overnight. Positions from day *d* are still tied up when day
*d+1*'s markets open, so events genuinely overlap and the portfolio spans days.
This is the case v3's per-day loop structurally cannot represent.

**Data requirement this creates:** the CLI *issuance timestamp* becomes the
settlement time. `weather.py` currently reads IEM's `entered` field only to sort
issuances and then discards it. `DailyMax` must carry it.

### D16 — Headline metric is profit per unit of peak capital at risk
Where **capital at risk** is the economic worst case, not cash deployed:

```
risk = max over states ω of ( cash_deployed − payoff(ω) )
```

The two differ materially. Buy YES>70 at 60¢ and NO>75 at 30¢: cash deployed is
90¢, but every outcome pays at least one leg, so the true worst case is 10¢. Using
cash deployed as the denominator would score a well-hedged strategy identically
to a naked one.

Across cities the latent states are independent, so the portfolio worst case is
the sum of per-event worst cases. This is what `A_yes` computes, and it is why the
payoff matrix earns its place in `core/risk.py` rather than sitting unused.

**Peak**, not mean — it answers "what bankroll did this actually need?". Mean is
reported alongside as a deployment-efficiency figure.

### D17 — Sharpe reported raw and annualised, with standard error
Daily returns on total equity. Both forms shown. With ~30 event-days the estimate
is extremely noisy, so the standard error is reported next to it rather than
letting a single number imply precision it does not have.

---

## Core types

```python
Cents = int          # all money
Price = int          # 1..99, YES price in cents

@dataclass(frozen=True)
class MarketSpec:
    ticker:      str
    event_key:   EventKey            # (city, event_date_local)
    strike_type: Literal["greater", "less", "between"]
    floor:       int | None          # °F
    cap:         int | None
    close_time:  datetime
    result:      Literal["yes", "no"] | None

@dataclass(frozen=True)
class Quote:
    ticker:  str
    ts:      datetime                # bar close, UTC
    yes_bid: Price | None
    yes_ask: Price | None
    volume:  int

@dataclass
class Position:
    net:      int                    # signed, YES units
    avg_cost: Price                  # cents per unit of the side held

@dataclass(frozen=True)
class OrderIntent:
    ticker:   str
    side:     Literal["yes", "no"]   # mirrors Kalshi's API
    action:   Literal["buy", "sell"]
    quantity: int
    reason:   str = ""

@dataclass(frozen=True)
class Fill:
    ts: datetime; ticker: str
    side: str; action: str
    quantity: int; price: Price; fee: Cents
```

`side` + `action` rather than v3's four fused verbs, because that is the shape of
Kalshi's own order API. The signed position (D2) is what the ledger keeps; the
verbs are what the strategy speaks.

---

## The clock

The engine consumes one ordered stream of two event kinds:

```
BAR(ts)              every live market's quote at that hourly timestamp
SETTLE(ts, event)    at the CLI issuance time on d+1
```

**Bars are hourly and UTC-aligned, so they coincide across cities** — bucketing by
timestamp and sorting the keys is sufficient. Settlement times are irregular, so
they interleave via a **two-way merge of two sorted sequences**, O(n + m).

No heap. I raised a k-way merge earlier and it is the wrong tool here: a heap
earns its place when many streams have irregular, independent timestamps. These
don't. Rule 2 — suitable, not sophisticated. If sub-hourly or unaligned data ever
arrives, the merge is the one function that changes.

Ties are broken by a monotonic `seq` counter assigned at build time. Without it,
two events at the same timestamp can be dispatched in either order and results
stop being reproducible.

---

## Data flow

```
DataSource ──► specs + quotes + daily maxima
                   │
                   ▼
              build gates          (lock bars, BLOCKED days)
                   │
                   ▼
              build clock          (bars ⋈ settlements, sorted, seq-tagged)
                   │
       ┌───────────┴───────────┐
       ▼                       ▼
   on BAR:                 on SETTLE:
   PortfolioState          pay out, zero positions,
   → strategy              release capital,
   → intents               record realised point
   → validate (gate,
     capital, volume)
   → fill FIFO
   → ledger
       │
       └──────────► daily equity marks ──► metrics
```

---

## Invariants

Numbered, testable, one test each in `tests/invariants/`.

- **I1 — No credit.** `free_cash ≥ 0` always.
- **I2 — Reservation balance.** `free_cash + reserved == total_cash`, and
  `reserved == 0` at the end of every bar (D10).
- **I3 — Cash conservation.** Over any interval, Δcash equals
  `− Σpremium_paid + Σpremium_received − Σfees + Σsettlement_payouts`. Exact in
  integers; no tolerance.
- **I4 — No naked short.** A sell moves `|net|` toward zero. `net` crosses zero
  only via flip selling, and only when capital covers the opening leg.
- **I5 — Monotone clock.** Every dispatched event has `ts ≥` its predecessor;
  ties resolved by `seq`.
- **I6 — No lookahead.** A fill at `ts` uses only quotes with timestamp `≤ ts`,
  and volume caps use bars strictly `< ts`.
- **I7 — Gate respected.** No order increasing `|net|` on a contract at or after
  its lock bar. A BLOCKED day produces zero orders.
- **I8 — Settlement exactness.** After settlement, position is zero and cash rose
  by exactly `100 × units_on_the_winning_side`.
- **I9 — Fee exactness.** Every fee is a non-negative integer cent count, ceiled
  over the whole order, never per contract.
- **I10 — Determinism.** Identical inputs produce a byte-identical trade list.

---

## Build order

Each step closes with a passing test.

| # | Step | Closing test |
|---|---|---|
| 0 | `types.py` + this invariant list | — it is the contract |
| 1 | `DataSource` + JSON adapter | bar counts match v3 |
| 2 | `clock.py` two-way merge | I5, I10 — sorted, stable, nothing lost or duplicated |
| 3 | `ledger.py` | I1–I4, I8, I9 under randomised trade sequences |
| 4 | `engine.py` dispatch | **single-city run reproduces v3's trade list exactly** |
| 5 | `risk.py` + `metrics/portfolio.py` | hand-computed toy portfolio |

Step 4 is load-bearing. Until v4 reproduces v3 on one city, any difference is a
bug in the new plumbing. Multi-city is switched on only after that passes —
*then* differences are the point.

Note: step 4's equality holds only with v4 configured to v3's semantics (no
volume cap, no capital constraint, settlement at close). Those are config flags,
which is itself a reason to have them.

---

## Open items

From `KALSHI_DOMAIN.md`, none blocking:

1. `KXHIGHLAX` / `KXHIGHMIA` series names still current.
2. `greater` / `less` boundary convention at an exact-strike settlement.
3. Whether the weather series carries a fee multiplier other than 1.
4. Whether Kalshi auto-nets an offsetting YES+NO holding in the same market.

Deferred by decision, not oversight: PostgreSQL storage (behind `DataSource`),
remote deployment, any C++ port.
