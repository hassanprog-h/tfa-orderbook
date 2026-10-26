"""Per-trader positions and profit & loss, built from the trades of a book.

Plug a :class:`Ledger` into a book with ``book.subscribe(ledger.on_event)``.
Amounts are kept in ticks (``price * quantity``) so they stay exact integers.
"""

from __future__ import annotations

from dataclasses import dataclass

from .models import Trade


@dataclass
class Account:
    trader_id: str
    position: int = 0  # units held: positive = long, negative = short
    cash: int = 0  # in ticks: money received from sales minus money paid for purchases
    volume: int = 0  # total units traded
    trades: int = 0

    def pnl(self, mark_price: float | None) -> float:
        """Mark-to-market profit: cash plus the value of the position at ``mark_price``."""
        # A position only exists after a trade, so with the last trade price as mark,
        # mark_price is None only when everybody is flat.
        return self.cash + self.position * (mark_price or 0)

    def average_price(self) -> float | None:
        """Break-even price of the current position (None when flat)."""
        return None if self.position == 0 else -self.cash / self.position


class Ledger:
    def __init__(self) -> None:
        self.accounts: dict[str, Account] = {}

    def account(self, trader_id: str) -> Account:
        if trader_id not in self.accounts:
            self.accounts[trader_id] = Account(trader_id)
        return self.accounts[trader_id]

    def on_event(self, event: object) -> None:
        if isinstance(event, Trade):
            self.apply(event)

    def apply(self, trade: Trade) -> None:
        notional = trade.price * trade.quantity
        if trade.buy_trader is not None:
            buyer = self.account(trade.buy_trader)
            buyer.position += trade.quantity
            buyer.cash -= notional
            buyer.volume += trade.quantity
            buyer.trades += 1
        if trade.sell_trader is not None:
            seller = self.account(trade.sell_trader)
            seller.position -= trade.quantity
            seller.cash += notional
            seller.volume += trade.quantity
            seller.trades += 1

    def leaderboard(self, mark_price: float | None) -> list[Account]:
        return sorted(self.accounts.values(), key=lambda a: a.pnl(mark_price), reverse=True)
