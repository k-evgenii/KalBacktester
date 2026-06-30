"""
engine.py — the core replay loop.

Per event-day, per hourly bar:
    1. build EventState (contracts visible at this bar)
    2. lazily attach market_state IF the strategy requires it
    3. ask the strategy for intents
    4. for each intent: gate check → fill@bid/ask → fee → ledger
    5. mark equity
After the day's bars: settle from Kalshi result and compute per-trade PnL.

Compared with V2 this is one readable function. The 15-step gauntlet
(z decode, multi-leg atomic fills, partial-fill config, deepcopy commit)
collapses because a simple strategy emits one leg and fills are independent.
The optimizer path re-enters only through `requires` + market_state.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from .fills import compute_fill, fee_for
from .gate import build_gate_from_max, is_locked
from .ledger import Ledger
from .schemas import Contract, DayGate, EventState, MarketSpec, Trade
from .strategy import Strategy

logger = logging.getLogger("kbt.engine")


class EventDay:
    """All inputs for one event-day, assembled by the runner."""
    def __init__(
        self,
        city_key:         str,
        event_date_local: date,
        specs:            list[MarketSpec],
        bars_by_ticker:   dict[str, list],          # ticker -> [Bar]
        daily_max=None,                              # kbt.weather.DailyMax | None
        blanket_time_stop: bool = True,              # hard rule: no entry at/after the max bar
    ) -> None:
        self.city_key = city_key
        self.event_date_local = event_date_local
        self.specs = sorted(specs, key=lambda s: s.threshold)
        self.bars_by_ticker = bars_by_ticker
        self.daily_max = daily_max
        self.gate = build_gate_from_max(city_key, event_date_local, self.specs,
                                        daily_max, blanket_time_stop=blanket_time_stop)

    def timeline(self) -> list[datetime]:
        ts = {b.ts for bars in self.bars_by_ticker.values() for b in bars}
        return sorted(ts)

    def state_at(self, ts: datetime) -> EventState:
        contracts: list[Contract] = []
        for spec in self.specs:
            bar = next((b for b in self.bars_by_ticker.get(spec.ticker, []) if b.ts == ts), None)
            if bar is None:
                continue
            contracts.append(Contract(spec=spec, bar=bar, q_implied=bar.mid))
        return EventState(
            city_key=self.city_key,
            event_date_local=self.event_date_local,
            ts=ts,
            contracts=contracts,
            gate=self.gate,
        )


def run_event(
    strategy:  Strategy,
    day:       EventDay,
    ledger:    Ledger,
    slippage:  float = 0.0,
    fee_rate:  float = 0.07,
) -> list[Trade]:
    """Run one event-day, mutating `ledger`, returning the day's trades."""
    needs_state = "market_state" in getattr(strategy, "requires", set())
    trades: list[Trade] = []

    # Weather-data safety: if the official daily max for this date could not be
    # trusted, the whole day is un-tradeable. Skip it — no entries, no fills.
    if getattr(day.gate, "blocked", False):
        logger.info("event-day %s %s BLOCKED (%s) — no entries",
                    day.city_key, day.event_date_local, day.gate.block_reason)
        return trades

    for ts in day.timeline():
        state = day.state_at(ts)
        if not state.contracts:
            continue

        # Lazy opt-in machinery — only the optimizer pays for this.
        if needs_state:
            from .statespace.builder import build_market_state
            state.market_state = build_market_state(state)

        try:
            intents = strategy.on_event(state)
        except Exception as exc:
            logger.error("strategy raised at %s: %s", ts, exc)
            raise

        for intent in intents:
            if intent.quantity <= 0:
                continue
            # gate: contract un-enterable once its outcome is observed-locked
            if intent.action.startswith("buy") and is_locked(day.gate, intent.ticker, ts):
                logger.debug("gate blocked %s at %s", intent.ticker, ts)
                continue
            bar = next((c.bar for c in state.contracts if c.spec.ticker == intent.ticker), None)
            if bar is None:
                continue
            fill = compute_fill(bar, intent.action, slippage=slippage)
            if not fill.ok:
                logger.debug("fill rejected %s: %s", intent.ticker, fill.reason)
                continue
            fee = fee_for(intent.quantity, fill.fill_price, fee_rate)
            ledger.apply(intent.ticker, intent.action, intent.quantity, fill.fill_price, fee)
            trades.append(Trade(
                ts=ts, city_key=day.city_key, event_date_local=day.event_date_local,
                ticker=intent.ticker, action=intent.action, quantity=intent.quantity,
                fill_price=fill.fill_price, fee=fee, reason=intent.reason,
            ))

        # equity mark at this bar
        ledger.mark(ts, {c.spec.ticker: (c.q_implied or 0.0) for c in state.contracts})

    # settle this day from Kalshi result
    results = {s.ticker: s.result for s in day.specs if s.result in ("yes", "no")}
    _settle_day_trades(trades, results)
    ledger.settle(results)
    ledger.record_realized(day.event_date_local)   # clean monotonic point
    return trades


def _settle_day_trades(trades: list[Trade], results: dict[str, str]) -> None:
    """Attach result + net PnL to each trade (for per-trade metrics)."""
    for t in trades:
        r = results.get(t.ticker)
        t.result = r
        if r is None:
            t.pnl = None
            continue
        # payoff of the side held by this action
        if t.action == "buy_yes":
            payoff = 1.0 if r == "yes" else 0.0
            t.pnl = t.quantity * (payoff - t.fill_price) - t.fee
        elif t.action == "buy_no":
            payoff = 1.0 if r == "no" else 0.0
            t.pnl = t.quantity * (payoff - t.fill_price) - t.fee
        elif t.action == "sell_yes":
            payoff = 1.0 if r == "yes" else 0.0
            t.pnl = t.quantity * (t.fill_price - payoff) - t.fee
        elif t.action == "sell_no":
            payoff = 1.0 if r == "no" else 0.0
            t.pnl = t.quantity * (t.fill_price - payoff) - t.fee