# Kalshi Domain Facts — carried forward from v3

Everything here is market structure, not implementation. It does not change when
we rewrite the engine. Each fact is tagged:

- **[C] Confirmed** — verified against real data or Kalshi's own fields.
- **[A] Assumed** — the v3 code depends on it and it has not visibly broken.
- **[U] Unverified** — v3 explicitly flagged this as needing a check.

---

## 1. Market taxonomy

| Level | Format | Example |
|---|---|---|
| Series | one per city | `KXHIGHNY` |
| Event | `{SERIES}-{YYMMMDD}` | `KXHIGHNY-26MAY14` |
| Market | event + strike suffix | one binary contract |

**[C]** One event = one city-day. Its markets form a *ladder* of binary contracts
over the same underlying (that day's high temperature). They are not independent
— this is the fact that breaks per-trade statistics.

**[C]** **Two events per city are open at once.** Today's and tomorrow's markets
trade simultaneously — at 07:33 local in Los Angeles, both the 12th and the 13th
are live. So a market's bars begin *before* its own event date, and the event key
must be the settlement date rather than the date the bars fall on.

Because daily markets settle on the next morning's report, three generations
overlap at any moment: yesterday awaiting settlement, today mid-observation,
tomorrow on pure forecast. Roughly 90 live markets across three cities.

The two open events are the same underlying under **different information
regimes** — today's high is partly realised, tomorrow's is entirely forecast.
That is the structural reason a portfolio view is needed rather than per-market
statistics.

| city_key | series | station | CLI PIL | station-name check |
|---|---|---|---|---|
| `new_york` | `KXHIGHNY` | KNYC | `CLINYC` | `CENTRAL PARK` |
| `los_angeles` | `KXHIGHLAX` | KLAX | `CLILAX` | `LOS ANGELES INTL AIRPORT` |
| `miami` | `KXHIGHMIA` | KMIA | `CLIMIA` | `MIAMI` |

**[U]** v3's `fetch_june.py` warns that `KXHIGHLAX` and `KXHIGHMIA` should be
re-checked on kalshi.com — Kalshi occasionally renames series. Symptom of a stale
name: zero markets returned for that city.

---

## 2. Strike types

From the Kalshi market record's `strike_type`, `floor_strike`, `cap_strike`:

| `strike_type` | YES resolves when | resolving number |
|---|---|---|
| `greater` | `high > floor_strike` | floor |
| `less` | `high <= cap_strike` | cap |
| `between` | `floor <= high <= cap` | floor |

**[U] Boundary asymmetry.** `greater` is strict (`>`), `less` is inclusive (`<=`).
v3 encodes this but derived it from documentation, not from a settled market at
the exact boundary. Worth confirming once against a real event where the high
landed exactly on a strike — it changes settlement on those days only, but it
changes it completely.

---

## 3. Prices and the book

**[C]** Kalshi is cent-denominated. A YES contract trades 1–99¢ and pays 100¢ if
YES resolves, 0 otherwise. NO is the exact complement.

**[C]** Book symmetry — there is one order book, quoted in YES terms:

```
NO ask = 100 − YES bid
NO bid = 100 − YES ask
```

**[A]** Crossing convention used by v3 (assumes a market maker is present, so a
marketable order fills immediately at the touch):

```
buy_yes   pay      yes_ask
sell_yes  receive  yes_bid
buy_no    pay      100 − yes_bid
sell_no   receive  100 − yes_ask
```

**[A]** A YES + NO pair on the same market is riskless and worth exactly 100¢.
Relevant to margin, not yet used by v3.

### Short semantics — verified

**[C]** A Kalshi event market is **one contract, not two**. You go long by buying
YES and short by buying NO. It behaves like a futures market, except every
position is fully collateralised at all times — Kalshi extends no credit, so
there is no margin call and no liquidation.

**[C]** **Naked shorting does not exist.** Sell orders are only available when
you already hold a position in that market. Selling YES you don't own is not a
thing; the equivalent trade is buying NO.

**[C]** **Flip selling** is an opt-in account setting covering the one case where
a sell exceeds your holding. Kalshi's own example: holding 5 NO and selling 10
sells the 5 NO and buys 5 YES for the remainder. So a single order *can* cross
through zero, but only when the setting is on.

**[C]** Buying YES is economically identical to selling NO; both have the same
effect on the book. The fee formula's `P × (1−P)` symmetry reflects this — the
fee at price `p` equals the fee at `100 − p`.

**Design consequence.** v3's four-verb action vocabulary with independent
`pos.yes` and `pos.no` counters (which can both go negative) does not describe
this market. The faithful representation is **one signed integer per market in
YES units**: positive is long YES, negative is long NO, zero is flat. Buys move
away from zero, sells move toward it, and flip selling is exactly the question of
whether a single order may cross zero. This is both more correct and simpler.

---

## 4. Fees

**[C]** Verified against Kalshi's fee schedule, July 2026 revision:

```
fee = round_up_to_cent( M × 0.07 × C × P × (1 − P) )
```

`P` = price in dollars, `C` = contract count, `M` = a per-contract multiplier
added in the July 2026 revision that **defaults to 1** for standard markets. So
v3's formula is correct for these weather markets. Some categories (crypto is
named) carry a higher multiplier.

**[C]** Rounding is up to the next cent **on the order as a whole**, not per
contract. v3's `ceil(raw × 100) / 100` on the full order matches. This makes
one-contract orders disproportionately expensive — a single contract at 50¢ pays
a full 1¢ fee, ~2% of the position.

**[C]** There is **no settlement fee** and no membership fee. v3 correctly
charges nothing at settlement.

**[A]** Maker orders are charged at roughly 25% of the taker rate (a 0.0175
coefficient). v3 always charges the taker rate, which is right when crossing the
touch — but see the fill-model note, because a mid fill is implicitly closer to a
maker execution.

The fee is maximised at P = 0.50 and vanishes at the tails, so fee drag is worst
exactly where an uncertain strategy trades most.

In integer cents, with `p` the YES price in cents, the taker fee is exact integer
arithmetic — no float anywhere:

```
fee_cents = ceil( 7 × C × p × (100 − p) / 10000 )
```

---

## 5. Settlement source — the load-bearing domain fact

**[C]** These markets settle on the **NWS Climatological Report (CLI) daily
MAXIMUM**, not on hourly station observations. v3 learned this the hard way:
KNYC hourly obs are sparse-to-empty and were never the settlement number anyway.

Consequences that shape the whole data layer:

- **[C]** The CLI for date *d* is issued the **morning of d+1**. To resolve day
  *d* you query products for *d+1* and verify the header says "FOR *d*".
- **[C]** History lives at the **IEM archive**. `api.weather.gov` retains only
  ~2 weeks, so it cannot backfill.
- **[C]** The CLI's *time of max* is **Local Standard Time year-round** — an NWS
  quirk. In summer it is one hour off the local wall clock. Hours to add to reach
  UTC: New York +5, Miami +5, Los Angeles +8.
- **[A]** The max **value** is treated as truth; the max **time** is advisory and
  carries a confidence tag.

### Fail-closed validation (all must pass, else the day is BLOCKED)

1. Header date equals the requested date.
2. Station name contains the city's expected string.
3. MAXIMUM parses to an integer in 20–130 °F.
4. Cross-check: `max − normal == departure` (±1), when both columns are present.

### Known CLI text quirks

| Token | Meaning | Handling |
|---|---|---|
| `95R` | record tied/set | strip the `R`, set `record_flag` |
| `320 PM` | missing colon | parse as 3:20 PM, confidence `assumed` |
| `12:50 PM` | well-formed | confidence `clean` |
| `MM` | missing | BLOCK |

Anything unrecognised blocks rather than guesses.

### Pending source change — 14 August 2026

**[U-pending]** A Kalshi notice states that from Friday 14 August 2026, daily
temperature markets move settlement from NWS to **The Weather Company**, with
official data at `weather.com/kalshi`. TWC uses NWS as its primary underlying
source.

Not independently confirmed. The Kalshi Help Center as of roughly three weeks
earlier still named the NWS Daily Climate Report for daily temperature markets,
with TWC used only for *hourly* temperature markets — so either the notice
post-dates that page or the help centre lags. Verify before relying on it.

What to watch when it lands:

- **Value discrepancies.** "Primary underlying source" is not "identical output".
  These brackets settle on integer °F, so a 1° difference flips the contract. The
  failure already exists under NWS: on 9 June 2026 KXHIGHCHI settled 88°F while
  the CLI read 87°F, attributed to a preliminary report and whole-°C rounding.
- **Publication timing.** If TWC publishes faster than the next-morning CLI, the
  overnight capital lock stops being true from 14 August onward.
- **Station identity.** Whether TWC reports the same station the CLI does.

The CLI/IEM pipeline remains correct and validated for dates before the change.
It should be kept, not replaced.

### Known settlement quirks

**[A]** Market determination can be delayed when a reported high is inconsistent
with the 6-hour or 24-hour figures — the report is revised rather than settled on.

---

## 6. No-lookahead gating

**[A]** Once the day's high has printed, the book goes effectively one-sided and
entries stop filling. v3 models this so the backtest cannot buy a contract whose
outcome is already known.

**Lock bar arithmetic.** A contract is locked from
`floor_to_hour(max_ts) + 1h` onward. A max at 18:24Z locks from the **19:00Z**
bar (the candle covering 18:00–19:00, which contains the max). The 18:00Z bar
(covering 17:00–18:00, entirely before the max) is still tradeable.

**Blanket time stop — default ON.** Every contract in the event is locked from
that bar, not just the decided ones. If the max time is unknown
(`time_confidence == "none"`), the rule cannot be enforced, so the **entire day
is BLOCKED** rather than risk a post-max fill.

**Per-contract locking** (v3's diagnostic alternative, off by default) uses only
information available at each hour:

| type | locks | when | why not the other side |
|---|---|---|---|
| `greater` | YES | `observed_max > floor` | high can still rise, so NO is never safe intraday |
| `less` | NO | `observed_max > cap` | same |
| `between` | NO | `observed_max > cap` | inside the bin ≠ decided; it can still rise out |

The asymmetry is the point: intraday you can only ever lock in the direction the
running max has already forced.

---

## 7. Bars

**[A]** Hourly OHLCV candles per market ticker, carrying `yes_bid_close` and
`yes_ask_close`. Mid = `(bid + ask) / 2`, falling back to `close` when a side is
missing. A bar with a missing side or crossed quotes is unfillable.

---

## 8. Status vocabulary — never conflate these

| Domain | Values | Meaning |
|---|---|---|
| Weather record | `OK` / `BLOCKED` | BLOCKED = untrustworthy settlement number → no entries that day |
| Fetch result | `OK` / `MISSING` / `ERROR` | MISSING = archive genuinely lacks it; ERROR = transport failure, safe to retry |

v3's rule, worth keeping: a transport failure must never be written to disk as
absent data. They are different facts and merging them silently corrupts the
backtest.

---

## Open items to verify before trusting absolute PnL

1. `KXHIGHLAX` / `KXHIGHMIA` series names still current.
2. The `greater` / `less` boundary convention at an exact-strike settlement.
3. Whether the weather series carries a fee multiplier other than 1.
4. Whether Kalshi auto-nets an offsetting YES+NO holding in the same market, or
   leaves both open. Only matters if we ever build a position that way.

## Resolved

- **Fees** — verified against the July 2026 schedule. v3's formula is correct.
- **Short semantics** — verified. No naked shorting; positions are one signed
  integer per market.
