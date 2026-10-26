"""Value types shared by the whole engine: sides, order types, orders, trades and reports.

All prices are integers expressed in *ticks* (the smallest price increment of the
instrument). Using integers instead of floats means ``100.1 + 0.2`` style rounding
errors can never make two equal prices compare as different. Conversion between a
human price like ``101.25`` and ticks lives in :mod:`orderbook.instrument`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Side(str, Enum):
    BUY = "buy"
    SELL = "sell"

    @property
    def opposite(self) -> Side:
        return Side.SELL if self is Side.BUY else Side.BUY

    @classmethod
    def parse(cls, value: Side | str) -> Side:
        """Accept ``Side.BUY`` as well as the strings ``"buy"`` / ``"BUY"``."""
        if isinstance(value, Side):
            return value
        try:
            return cls(str(value).lower())
        except ValueError:
            raise ValueError(f"side must be 'buy' or 'sell', got {value!r}") from None


class OrderType(str, Enum):
    LIMIT = "limit"  # trades at its price or better, the rest may rest in the book
    MARKET = "market"  # trades at any price, never rests


class TimeInForce(str, Enum):
    GTC = "gtc"  # good-till-cancelled: the unfilled part rests in the book
    IOC = "ioc"  # immediate-or-cancel: fill what is possible now, cancel the rest
    FOK = "fok"  # fill-or-kill: fill the whole quantity now, or do nothing at all


class OrderStatus(str, Enum):
    NEW = "new"  # resting in the book, nothing filled yet
    PARTIALLY_FILLED = "partially_filled"  # resting in the book, partly filled
    FILLED = "filled"  # fully executed
    CANCELLED = "cancelled"  # removed before being fully filled (may have partial fills)
    REJECTED = "rejected"  # refused, nothing happened

    @property
    def is_open(self) -> bool:
        return self in (OrderStatus.NEW, OrderStatus.PARTIALLY_FILLED)


class SelfTradePrevention(str, Enum):
    """What to do when an incoming order would trade against an order of the same trader."""

    NONE = "none"  # allow the self-trade
    CANCEL_RESTING = "cancel_resting"  # cancel the resting order and keep matching
    CANCEL_INCOMING = "cancel_incoming"  # cancel what is left of the incoming order
    CANCEL_BOTH = "cancel_both"  # cancel both


@dataclass(slots=True)
class Order:
    order_id: int
    side: Side
    price: int | None  # in ticks; None for market orders
    quantity: int  # total quantity ever asked for (filled + remaining)
    remaining: int  # quantity still open
    sequence: int  # arrival number, gives time priority (lower = earlier)
    trader_id: str | None = None
    order_type: OrderType = OrderType.LIMIT
    tif: TimeInForce = TimeInForce.GTC
    post_only: bool = False
    status: OrderStatus = OrderStatus.NEW

    @property
    def filled(self) -> int:
        return self.quantity - self.remaining


@dataclass(frozen=True, slots=True)
class Trade:
    trade_id: int
    buy_order_id: int
    sell_order_id: int
    price: int  # always the resting order's price
    quantity: int
    aggressor: Side  # side of the incoming order that caused the trade
    buy_trader: str | None = None
    sell_trader: str | None = None


@dataclass(frozen=True, slots=True)
class OrderUpdate:
    """Event emitted every time the status or open quantity of an order changes."""

    order_id: int
    side: Side
    price: int | None
    status: OrderStatus
    remaining: int
    filled: int
    trader_id: str | None = None
    reason: str | None = None


@dataclass(slots=True)
class ExecutionReport:
    """What the caller gets back after submitting (or modifying) an order."""

    order_id: int
    status: OrderStatus
    filled_quantity: int
    remaining_quantity: int
    trades: list[Trade] = field(default_factory=list)
    reason: str | None = None  # why the order was rejected or (partly) cancelled

    @property
    def average_price(self) -> float | None:
        """Volume-weighted average fill price in ticks (None if nothing traded)."""
        if not self.trades:
            return None
        notional = sum(t.price * t.quantity for t in self.trades)
        return notional / sum(t.quantity for t in self.trades)


@dataclass(frozen=True, slots=True)
class Level:
    price: int
    quantity: int  # total open quantity at this price
    orders: int  # number of orders at this price


@dataclass(frozen=True, slots=True)
class Depth:
    """Aggregated view of the top N price levels (a.k.a. "L2" market data)."""

    bids: list[Level]  # best (highest) first
    asks: list[Level]  # best (lowest) first
