"""The exchange service behind the web UI.

It wraps an :class:`~orderbook.OrderBook` with everything a front-end needs but the
engine should not care about: decimal prices, wall-clock timestamps, a trade tape,
OHLC candles, per-trader accounts and an activity log. The engine itself stays pure
and deterministic; this layer is the only place that reads the clock.
"""

from __future__ import annotations

import time
from collections import deque
from decimal import Decimal
from typing import Any

from orderbook import (
    Instrument,
    Journal,
    Ledger,
    Order,
    OrderBook,
    OrderUpdate,
    SelfTradePrevention,
    Trade,
)


class OrderNotFound(LookupError):
    pass


class Exchange:
    def __init__(
        self,
        instrument: Instrument | None = None,
        candle_seconds: int = 2,
    ) -> None:
        self.instrument = instrument or Instrument("TFA", Decimal("0.01"))
        self.candle_seconds = candle_seconds
        self.bots: set[str] = set()
        self.version = 0
        self.reset()

    def reset(self) -> None:
        self.journal = Journal()
        self.book = OrderBook(SelfTradePrevention.CANCEL_RESTING, journal=self.journal)
        self.ledger = Ledger()
        self.book.subscribe(self.ledger.on_event)
        self.book.subscribe(self._on_event)
        self.trades: deque[dict[str, Any]] = deque(maxlen=100)
        self.candles: deque[dict[str, Any]] = deque(maxlen=150)
        self.activity: deque[dict[str, Any]] = deque(maxlen=100)
        self.open_price: int | None = None
        self.high: int | None = None
        self.low: int | None = None
        self.volume = 0
        self.trade_count = 0
        self.touch()

    def touch(self) -> None:
        """Mark the state as changed so that it gets pushed to the browsers."""
        self.version += 1

    # ---------------------------------------------------------------- commands

    def place(
        self,
        trader: str,
        side: str,
        quantity: int,
        order_type: str = "limit",
        price: Decimal | str | float | None = None,
        tif: str | None = None,
        post_only: bool = False,
    ) -> dict[str, Any]:
        if order_type == "market":
            report = self.book.market(side, quantity, trader_id=trader, tif=tif or "ioc")
        elif order_type == "limit":
            if price is None:
                raise ValueError("a limit order needs a price")
            ticks = self.instrument.to_ticks(price)
            report = self.book.limit(
                side, ticks, quantity, trader_id=trader, tif=tif or "gtc", post_only=post_only
            )
        else:
            raise ValueError(f"unknown order type {order_type!r}")
        self.touch()
        return {
            "order_id": report.order_id,
            "status": report.status.value,
            "filled": report.filled_quantity,
            "resting": report.remaining_quantity,
            "average_price": self._avg(report.average_price),
            "trades": [self._trade_dict(t) for t in report.trades],
            "reason": report.reason,
        }

    def cancel(self, order_id: int, trader: str) -> None:
        self._owned(order_id, trader)
        self.book.cancel(order_id)
        self.touch()

    def cancel_all(self, trader: str) -> int:
        orders = self.book.open_orders(trader)
        for order in orders:
            self.book.cancel(order.order_id)
        self.touch()
        return len(orders)

    def modify(
        self,
        order_id: int,
        trader: str,
        price: Decimal | str | float | None = None,
        quantity: int | None = None,
    ) -> dict[str, Any]:
        self._owned(order_id, trader)
        ticks = None if price is None else self.instrument.to_ticks(price)
        report = self.book.modify(order_id, price=ticks, quantity=quantity)
        self.touch()
        return {
            "order_id": report.order_id,
            "status": report.status.value,
            "filled": report.filled_quantity,
            "resting": report.remaining_quantity,
            "trades": [self._trade_dict(t) for t in report.trades],
            "reason": report.reason,
        }

    def _owned(self, order_id: int, trader: str) -> Order:
        order = self.book.get_order(order_id)
        if order is None or order.trader_id != trader:
            raise OrderNotFound(f"no open order #{order_id} for {trader}")
        return order

    # ---------------------------------------------------------------- events

    def _on_event(self, event: Trade | OrderUpdate) -> None:
        now = time.time()
        if isinstance(event, Trade):
            self._record_trade(event, now)
        elif event.trader_id is not None and event.trader_id not in self.bots:
            self.activity.appendleft(
                {
                    "ts": now,
                    "trader": event.trader_id,
                    "order_id": event.order_id,
                    "side": event.side.value,
                    "price": self._price(event.price),
                    "status": event.status.value,
                    "filled": event.filled,
                    "remaining": event.remaining,
                    "reason": event.reason,
                }
            )

    def _record_trade(self, trade: Trade, now: float) -> None:
        self.trades.appendleft({**self._trade_dict(trade), "ts": now})
        self.volume += trade.quantity
        self.trade_count += 1
        if self.open_price is None:
            self.open_price = trade.price
        self.high = trade.price if self.high is None else max(self.high, trade.price)
        self.low = trade.price if self.low is None else min(self.low, trade.price)

        bucket = int(now // self.candle_seconds) * self.candle_seconds
        candle = self.candles[-1] if self.candles else None
        if candle is None or candle["t"] != bucket:
            self.candles.append(
                {
                    "t": bucket,
                    "o": trade.price,
                    "h": trade.price,
                    "l": trade.price,
                    "c": trade.price,
                    "v": trade.quantity,
                }
            )
        else:
            candle["h"] = max(candle["h"], trade.price)
            candle["l"] = min(candle["l"], trade.price)
            candle["c"] = trade.price
            candle["v"] += trade.quantity

    # ---------------------------------------------------------------- views

    def _price(self, ticks: int | None) -> float | None:
        """Ticks -> price for JSON. Exact: the Decimal is only turned into a float at the end."""
        return None if ticks is None else float(self.instrument.to_price(ticks))

    def _avg(self, ticks: float | None) -> float | None:
        """Same for values that are not whole ticks (averages, mid price)."""
        return None if ticks is None else round(ticks * float(self.instrument.tick_size), 6)

    def _trade_dict(self, trade: Trade) -> dict[str, Any]:
        return {
            "id": trade.trade_id,
            "price": self._price(trade.price),
            "quantity": trade.quantity,
            "aggressor": trade.aggressor.value,
            "buyer": trade.buy_trader,
            "seller": trade.sell_trader,
            "buy_order_id": trade.buy_order_id,
            "sell_order_id": trade.sell_order_id,
        }

    def order_dict(self, order: Order) -> dict[str, Any]:
        return {
            "id": order.order_id,
            "trader": order.trader_id,
            "side": order.side.value,
            "price": self._price(order.price),
            "quantity": order.quantity,
            "filled": order.filled,
            "remaining": order.remaining,
            "status": order.status.value,
        }

    def accounts(self) -> list[dict[str, Any]]:
        mark = self.book.last_trade.price if self.book.last_trade else None
        tick = float(self.instrument.tick_size)
        return [
            {
                "trader": a.trader_id,
                "bot": a.trader_id in self.bots,
                "position": a.position,
                "cash": round(a.cash * tick, 2),
                "pnl": round(a.pnl(mark) * tick, 2),
                "average_price": self._avg(a.average_price()),
                "volume": a.volume,
                "trades": a.trades,
            }
            for a in self.ledger.leaderboard(mark)
        ]

    def depth(self, levels: int) -> dict[str, list[list[float | int]]]:
        depth = self.book.depth(levels)
        return {
            "bids": [[self._price(lvl.price), lvl.quantity, lvl.orders] for lvl in depth.bids],
            "asks": [[self._price(lvl.price), lvl.quantity, lvl.orders] for lvl in depth.asks],
        }

    def book_view(self, levels: int) -> dict[str, Any]:
        return {
            "best_bid": self._price(self.book.best_bid()),
            "best_ask": self._price(self.book.best_ask()),
            **self.depth(levels),
        }

    def state(self, levels: int = 30) -> dict[str, Any]:
        book = self.book
        last = book.last_trade.price if book.last_trade else None
        human_orders = [o for o in book.open_orders() if o.trader_id not in self.bots]
        return {
            "version": self.version,
            "symbol": self.instrument.symbol,
            "tick_size": float(self.instrument.tick_size),
            "book": self.depth(levels),
            "best_bid": self._price(book.best_bid()),
            "best_ask": self._price(book.best_ask()),
            "spread": self._price(book.spread()),
            "mid": self._avg(book.mid_price()),
            "last": self._price(last),
            "open": self._price(self.open_price),
            "high": self._price(self.high),
            "low": self._price(self.low),
            "volume": self.volume,
            "trade_count": self.trade_count,
            "resting_orders": len(book),
            "journal_entries": len(self.journal.entries),
            "trades": list(self.trades)[:60],
            "candles": [{**c, **{k: self._price(c[k]) for k in "ohlc"}} for c in self.candles],
            "orders": [self.order_dict(o) for o in human_orders],
            "accounts": self.accounts(),
            "activity": list(self.activity)[:60],
        }
