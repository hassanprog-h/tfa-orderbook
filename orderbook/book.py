"""The order book and its matching engine.

Data structures
---------------
Each side of the book (:class:`BookSide`) is made of:

* ``levels``: a ``dict`` mapping a price to a :class:`PriceLevel`. Finding the level of
  a given price is O(1).
* a heap of prices, so that the best price (highest bid / lowest ask) is found in
  O(1) and a new price level is inserted in O(log L), L being the number of levels.
  Empty levels are removed from ``levels`` right away and from the heap lazily, the
  next time they show up at the top.

A :class:`PriceLevel` keeps its orders in an ``OrderedDict`` keyed by order id: it is a
FIFO queue (time priority) that *also* supports removing any order in O(1), which is
exactly what cancelling needs. A plain ``deque`` would make cancel O(n).

Finally ``OrderBook._orders`` indexes every resting order by id, so cancel and modify
never have to search the book.

Price priority comes from the heap, time priority from the FIFO queue of each level:
together they give price-time priority by construction.
"""

from __future__ import annotations

import heapq
from collections import OrderedDict
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING, Union

from .models import (
    Depth,
    ExecutionReport,
    Level,
    Order,
    OrderStatus,
    OrderType,
    OrderUpdate,
    SelfTradePrevention,
    Side,
    TimeInForce,
    Trade,
)

if TYPE_CHECKING:
    from .journal import Journal

Event = Union[Trade, OrderUpdate]
Listener = Callable[[Event], None]


class PriceLevel:
    """All the resting orders at one price, in arrival order."""

    __slots__ = ("price", "orders", "total_quantity")

    def __init__(self, price: int) -> None:
        self.price = price
        self.orders: OrderedDict[int, Order] = OrderedDict()
        self.total_quantity = 0

    def append(self, order: Order) -> None:
        self.orders[order.order_id] = order
        self.total_quantity += order.remaining

    def first(self) -> Order:
        """The oldest order at this price: the next one to trade."""
        return next(iter(self.orders.values()))

    def remove(self, order: Order) -> None:
        del self.orders[order.order_id]
        self.total_quantity -= order.remaining

    def __len__(self) -> int:
        return len(self.orders)


class BookSide:
    """The bids or the asks."""

    def __init__(self, side: Side) -> None:
        self.side = side
        self.levels: dict[int, PriceLevel] = {}
        # heapq is a min-heap: store asks as ``price`` and bids as ``-price`` so that the
        # top of the heap is always the best price of the side.
        self._heap: list[int] = []
        self._in_heap: set[int] = set()

    def _key(self, price: int) -> int:
        return -price if self.side is Side.BUY else price

    def best_level(self) -> PriceLevel | None:
        heap = self._heap
        while heap:
            price = self._key(heap[0])  # _key is its own inverse
            level = self.levels.get(price)
            if level is not None:
                return level
            heapq.heappop(heap)  # stale entry: the level was emptied earlier
            self._in_heap.discard(price)
        return None

    def best_price(self) -> int | None:
        level = self.best_level()
        return None if level is None else level.price

    def add(self, order: Order) -> None:
        assert order.price is not None
        level = self.levels.get(order.price)
        if level is None:
            level = self.levels[order.price] = PriceLevel(order.price)
            # If the price is still in the heap as a stale entry, it simply becomes
            # valid again, so each price appears at most once in the heap.
            if order.price not in self._in_heap:
                heapq.heappush(self._heap, self._key(order.price))
                self._in_heap.add(order.price)
        level.append(order)

    def remove(self, order: Order) -> None:
        assert order.price is not None
        level = self.levels[order.price]
        level.remove(order)
        if not level.orders:
            del self.levels[order.price]

    def is_better_or_equal(self, price: int, limit: int) -> bool:
        """True if a resting ``price`` on this side is at least as good as ``limit``
        for someone trading against it (lower for asks, higher for bids)."""
        return price <= limit if self.side is Side.SELL else price >= limit

    def sorted_prices(self) -> list[int]:
        return sorted(self.levels, key=self._key)

    def top(self, n: int) -> list[Level]:
        prices = heapq.nsmallest(n, self.levels, key=self._key)
        return [
            Level(p, self.levels[p].total_quantity, len(self.levels[p].orders)) for p in prices
        ]

    def __iter__(self) -> Iterator[PriceLevel]:
        """Levels from best to worst."""
        for price in self.sorted_prices():
            yield self.levels[price]


class OrderBook:
    """A limit order book for a single instrument, with price-time priority matching.

    >>> book = OrderBook()
    >>> book.limit("sell", 101, 5).order_id
    1
    >>> report = book.limit("buy", 102, 3)
    >>> [(t.sell_order_id, t.price, t.quantity) for t in report.trades]
    [(1, 101, 3)]
    """

    def __init__(
        self,
        self_trade_prevention: SelfTradePrevention | str = SelfTradePrevention.NONE,
        journal: Journal | None = None,
    ) -> None:
        self.bids = BookSide(Side.BUY)
        self.asks = BookSide(Side.SELL)
        self.self_trade_prevention = SelfTradePrevention(self_trade_prevention)
        self.last_trade: Trade | None = None
        self._orders: dict[int, Order] = {}  # resting orders only
        self._next_order_id = 1
        self._next_sequence = 1
        self._next_trade_id = 1
        self._listeners: list[Listener] = []
        self.journal = journal
        if journal is not None:
            journal.start(self)

    # ------------------------------------------------------------------ commands

    def limit(
        self,
        side: Side | str,
        price: int,
        quantity: int,
        *,
        trader_id: str | None = None,
        tif: TimeInForce | str = TimeInForce.GTC,
        post_only: bool = False,
    ) -> ExecutionReport:
        """Submit a limit order. Returns a report holding the new order id and its trades."""
        side, tif = Side.parse(side), TimeInForce(tif)
        _check_price(price)
        _check_quantity(quantity)
        if post_only and tif is not TimeInForce.GTC:
            raise ValueError("a post-only order must be GTC (it exists to rest in the book)")
        self._record("limit", side=side.value, price=price, quantity=quantity,
                     trader_id=trader_id, tif=tif.value, post_only=post_only)
        order = self._new_order(side, price, quantity, trader_id, OrderType.LIMIT, tif, post_only)
        return self._process(order)

    def market(
        self,
        side: Side | str,
        quantity: int,
        *,
        trader_id: str | None = None,
        tif: TimeInForce | str = TimeInForce.IOC,
    ) -> ExecutionReport:
        """Submit a market order: it takes the best prices available and never rests.

        With ``tif="fok"`` it is executed only if the whole quantity can be filled.
        """
        side, tif = Side.parse(side), TimeInForce(tif)
        _check_quantity(quantity)
        if tif is TimeInForce.GTC:
            raise ValueError("a market order cannot rest in the book: use IOC or FOK")
        self._record("market", side=side.value, quantity=quantity, trader_id=trader_id,
                     tif=tif.value)
        order = self._new_order(side, None, quantity, trader_id, OrderType.MARKET, tif, False)
        return self._process(order)

    def cancel(self, order_id: int) -> bool:
        """Cancel a resting order. Returns False if it is unknown or no longer open."""
        self._record("cancel", order_id=order_id)
        order = self._orders.get(order_id)
        if order is None:
            return False
        self._remove_resting(order, "cancelled by user")
        return True

    def modify(
        self, order_id: int, *, price: int | None = None, quantity: int | None = None
    ) -> ExecutionReport:
        """Change the price and/or the open quantity of a resting order.

        Like on real exchanges, *reducing* the quantity at the same price keeps the
        order's place in the queue. Changing the price or increasing the quantity
        sends the order to the back of the queue (it gets a new sequence number, but
        keeps its id) and it may trade immediately if the new price crosses.
        """
        if price is not None:
            _check_price(price)
        if quantity is not None:
            _check_quantity(quantity)
        self._record("modify", order_id=order_id, price=price, quantity=quantity)

        order = self._orders.get(order_id)
        if order is None:
            return ExecutionReport(order_id, OrderStatus.REJECTED, 0, 0,
                                   reason="unknown or closed order")
        new_price = order.price if price is None else price
        new_remaining = order.remaining if quantity is None else quantity

        if new_price == order.price and new_remaining <= order.remaining:
            # In-place reduction: keeps time priority.
            reduction = order.remaining - new_remaining
            self._side(order.side).levels[order.price].total_quantity -= reduction
            order.remaining -= reduction
            order.quantity -= reduction
            self._emit_order(order, "modified")
            return self._report(order, [], None)

        # Cancel/replace: lose time priority, may cross the spread.
        self._side(order.side).remove(order)
        del self._orders[order.order_id]
        order.quantity = order.filled + new_remaining
        order.remaining = new_remaining
        order.price = new_price
        order.sequence = self._take_sequence()
        return self._process(order)

    # ------------------------------------------------------------------ queries

    def best_bid(self) -> int | None:
        return self.bids.best_price()

    def best_ask(self) -> int | None:
        return self.asks.best_price()

    def spread(self) -> int | None:
        bid, ask = self.best_bid(), self.best_ask()
        return None if bid is None or ask is None else ask - bid

    def mid_price(self) -> float | None:
        bid, ask = self.best_bid(), self.best_ask()
        return None if bid is None or ask is None else (bid + ask) / 2

    def depth(self, levels: int = 5) -> Depth:
        """Total quantity at each of the top ``levels`` prices on both sides."""
        if levels < 0:
            raise ValueError("levels must be >= 0")
        return Depth(bids=self.bids.top(levels), asks=self.asks.top(levels))

    def get_order(self, order_id: int) -> Order | None:
        """The resting order with this id, or None."""
        return self._orders.get(order_id)

    def open_orders(self, trader_id: str | None = None) -> list[Order]:
        orders = sorted(self._orders.values(), key=lambda o: o.order_id)
        if trader_id is None:
            return orders
        return [o for o in orders if o.trader_id == trader_id]

    def snapshot(self) -> dict[str, list[tuple[int, list[tuple[int, int]]]]]:
        """Full order-by-order view ("L3"): for each level, (order id, open qty) in queue order."""
        return {
            side.side.value: [
                (level.price, [(o.order_id, o.remaining) for o in level.orders.values()])
                for level in side
            ]
            for side in (self.bids, self.asks)
        }

    def __len__(self) -> int:
        """Number of resting orders."""
        return len(self._orders)

    # ------------------------------------------------------------------ events

    def subscribe(self, listener: Listener) -> Callable[[], None]:
        """Call ``listener`` with every :class:`Trade` and :class:`OrderUpdate`.

        Returns a function that unsubscribes the listener.
        """
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    # ------------------------------------------------------------------ internals

    def _side(self, side: Side) -> BookSide:
        return self.bids if side is Side.BUY else self.asks

    def _take_sequence(self) -> int:
        seq = self._next_sequence
        self._next_sequence += 1
        return seq

    def _new_order(self, side, price, quantity, trader_id, order_type, tif, post_only) -> Order:
        order = Order(
            order_id=self._next_order_id,
            side=side,
            price=price,
            quantity=quantity,
            remaining=quantity,
            sequence=self._take_sequence(),
            trader_id=trader_id,
            order_type=order_type,
            tif=tif,
            post_only=post_only,
        )
        self._next_order_id += 1
        return order

    def _crosses(self, order: Order, resting_price: int) -> bool:
        if order.price is None:  # market order: any price is acceptable
            return True
        if order.side is Side.BUY:
            return resting_price <= order.price
        return resting_price >= order.price

    def _is_self_trade(self, incoming: Order, resting: Order) -> bool:
        return (
            self.self_trade_prevention is not SelfTradePrevention.NONE
            and incoming.trader_id is not None
            and incoming.trader_id == resting.trader_id
        )

    def _process(self, order: Order) -> ExecutionReport:
        """Run a new (or replaced) order through the book."""
        opposite = self._side(order.side.opposite)

        if order.post_only:
            best = opposite.best_price()
            if best is not None and self._crosses(order, best):
                return self._close(order, OrderStatus.REJECTED, [],
                                   "post-only order would take liquidity")

        if order.tif is TimeInForce.FOK and self._fillable(order) < order.remaining:
            return self._close(order, OrderStatus.CANCELLED, [],
                               "fill-or-kill order cannot be fully filled")

        trades, stp_cancelled = self._match(order, opposite)

        if order.remaining == 0:
            return self._close(order, OrderStatus.FILLED, trades, None)
        if stp_cancelled:
            return self._close(order, OrderStatus.CANCELLED, trades, "self-trade prevention")
        if order.order_type is OrderType.MARKET or order.tif is not TimeInForce.GTC:
            reason = "no liquidity left" if order.price is None else "unfilled part cancelled (IOC)"
            return self._close(order, OrderStatus.CANCELLED, trades, reason)

        # Whatever is left becomes a resting order.
        order.status = OrderStatus.PARTIALLY_FILLED if order.filled else OrderStatus.NEW
        self._side(order.side).add(order)
        self._orders[order.order_id] = order
        self._emit_order(order, None)
        return self._report(order, trades, None)

    def _match(self, order: Order, opposite: BookSide) -> tuple[list[Trade], bool]:
        """Trade ``order`` against the opposite side while prices cross.

        Returns the trades and whether the order was stopped by self-trade prevention.
        """
        trades: list[Trade] = []
        while order.remaining > 0:
            level = opposite.best_level()
            if level is None or not self._crosses(order, level.price):
                break
            resting = level.first()  # oldest order at the best price

            if self._is_self_trade(order, resting):
                policy = self.self_trade_prevention
                if policy in (SelfTradePrevention.CANCEL_RESTING, SelfTradePrevention.CANCEL_BOTH):
                    self._remove_resting(resting, "self-trade prevention")
                if policy in (SelfTradePrevention.CANCEL_INCOMING,
                              SelfTradePrevention.CANCEL_BOTH):
                    return trades, True
                continue

            quantity = min(order.remaining, resting.remaining)
            trades.append(self._execute(order, resting, level, quantity))
        return trades, False

    def _execute(self, incoming: Order, resting: Order, level: PriceLevel, quantity: int) -> Trade:
        buy, sell = (incoming, resting) if incoming.side is Side.BUY else (resting, incoming)
        trade = Trade(
            trade_id=self._next_trade_id,
            buy_order_id=buy.order_id,
            sell_order_id=sell.order_id,
            price=level.price,  # the resting order's price
            quantity=quantity,
            aggressor=incoming.side,
            buy_trader=buy.trader_id,
            sell_trader=sell.trader_id,
        )
        self._next_trade_id += 1
        self.last_trade = trade

        incoming.remaining -= quantity
        resting.remaining -= quantity
        level.total_quantity -= quantity
        if resting.remaining == 0:
            resting.status = OrderStatus.FILLED
            self._side(resting.side).remove(resting)
            del self._orders[resting.order_id]
        else:
            resting.status = OrderStatus.PARTIALLY_FILLED

        self._emit(trade)
        self._emit_order(resting, None)
        return trade

    def _fillable(self, order: Order) -> int:
        """How much of ``order`` could be filled right now, without touching the book.

        Used by fill-or-kill. It walks the crossing levels in priority order and takes
        self-trade prevention into account, so the answer matches what ``_match`` does.
        """
        opposite = self._side(order.side.opposite)
        policy = self.self_trade_prevention
        available = 0
        for price in opposite.sorted_prices():
            if not self._crosses(order, price):
                break
            for resting in opposite.levels[price].orders.values():
                if self._is_self_trade(order, resting):
                    if policy is SelfTradePrevention.CANCEL_RESTING:
                        continue
                    return available  # matching would stop here
                available += resting.remaining
                if available >= order.remaining:
                    return available
        return available

    def _remove_resting(self, order: Order, reason: str) -> None:
        self._side(order.side).remove(order)
        del self._orders[order.order_id]
        order.status = OrderStatus.CANCELLED
        self._emit_order(order, reason)

    def _close(self, order: Order, status: OrderStatus, trades: list[Trade],
               reason: str | None) -> ExecutionReport:
        """Finish an incoming order that does not (or no longer) rest in the book."""
        order.status = status
        self._emit_order(order, reason)
        return self._report(order, trades, reason)

    def _report(self, order: Order, trades: list[Trade], reason: str | None) -> ExecutionReport:
        resting = order.remaining if order.status.is_open else 0
        return ExecutionReport(order.order_id, order.status, order.filled, resting, trades, reason)

    def _emit(self, event: Event) -> None:
        for listener in list(self._listeners):
            listener(event)

    def _emit_order(self, order: Order, reason: str | None) -> None:
        if self._listeners:
            self._emit(OrderUpdate(order.order_id, order.side, order.price, order.status,
                                   order.remaining if order.status.is_open else 0,
                                   order.filled, order.trader_id, reason))

    def _record(self, command: str, **args) -> None:
        if self.journal is not None:
            self.journal.record(command, args)


def _check_quantity(quantity: int) -> None:
    if isinstance(quantity, bool) or not isinstance(quantity, int):
        raise TypeError(f"quantity must be an int, got {type(quantity).__name__}")
    if quantity <= 0:
        raise ValueError(f"quantity must be positive, got {quantity}")


def _check_price(price: int) -> None:
    if isinstance(price, bool) or not isinstance(price, int):
        raise TypeError(
            f"price must be an int number of ticks, got {type(price).__name__} "
            "(use Instrument.to_ticks to convert a decimal price)"
        )
    if price <= 0:
        raise ValueError(f"price must be positive, got {price}")
