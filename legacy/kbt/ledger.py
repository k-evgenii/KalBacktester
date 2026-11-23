"""
ledger.py — cash + per-contract positions, settlement, equity curve.

Cash-flow accounting (no latent-state geometry needed for the simple path):
    buy  → cash -= qty * price + fee ; position += qty
    sell → cash += qty * price - fee ; position -= qty
At settlement, each held YES contract pays $1 if result=="yes" (else $0),
each held NO contract pays $1 if result=="no" (else $0).

Realized PnL = final cash (starting at 0). The equity curve marks open
positions at the current bar mid so you can see the path, not just the endpoint.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from .schemas import Action, Trade


@dataclass
class Position:
    yes: int = 0
    no:  int = 0


@dataclass
class Ledger:
    cash:      float = 0.0
    positions: dict[str, Position] = field(default_factory=dict)
    equity_curve: list[tuple[datetime, float]] = field(default_factory=list)
    # realized cumulative PnL, one point per settled event-day (monotonic, clean)
    realized_curve: list[tuple[object, float]] = field(default_factory=list)

    def _pos(self, ticker: str) -> Position:
        return self.positions.setdefault(ticker, Position())

    def apply(self, ticker: str, action: Action, qty: int, price: float, fee: float) -> None:
        pos = self._pos(ticker)
        if action == "buy_yes":
            self.cash -= qty * price + fee;  pos.yes += qty
        elif action == "sell_yes":
            self.cash += qty * price - fee;  pos.yes -= qty
        elif action == "buy_no":
            self.cash -= qty * price + fee;  pos.no += qty
        elif action == "sell_no":
            self.cash += qty * price - fee;  pos.no -= qty

    def mark(self, ts: datetime, yes_mid: dict[str, float]) -> None:
        """Append an equity point: cash + open positions marked at YES mid."""
        mtm = self.cash
        for ticker, pos in self.positions.items():
            mid = yes_mid.get(ticker)
            if mid is None:
                continue
            mtm += pos.yes * mid + pos.no * (1.0 - mid)
        self.equity_curve.append((ts, mtm))

    def settle(self, results: dict[str, str]) -> None:
        """results: {ticker -> 'yes'|'no'}. Pays out then closes settled positions."""
        for ticker, pos in self.positions.items():
            r = results.get(ticker)
            if r == "yes":
                self.cash += pos.yes * 1.0
            elif r == "no":
                self.cash += pos.no * 1.0
        # closed contracts are done — drop them so later marks don't revalue them
        for ticker in list(self.positions):
            if results.get(ticker) in ("yes", "no"):
                del self.positions[ticker]

    def record_realized(self, label) -> None:
        """Append a realized cumulative-PnL point (call AFTER settle)."""
        self.realized_curve.append((label, self.cash))
