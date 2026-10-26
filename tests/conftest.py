from __future__ import annotations

import pytest

from orderbook import OrderBook, Side


def check_invariants(book: OrderBook) -> None:
    """Structural properties that must hold after every operation."""
    bid, ask = book.best_bid(), book.best_ask()
    assert bid is None or ask is None or bid < ask, f"book is crossed: {bid} >= {ask}"

    seen = set()
    for side in (book.bids, book.asks):
        for price, level in side.levels.items():
            assert level.orders, f"empty level left at {price}"
            assert level.total_quantity == sum(o.remaining for o in level.orders.values())
            sequences = [o.sequence for o in level.orders.values()]
            assert sequences == sorted(sequences), "time priority broken inside a level"
            for order in level.orders.values():
                assert order.price == price and order.side is side.side
                assert order.remaining > 0 and order.status.is_open
                assert book.get_order(order.order_id) is order
                seen.add(order.order_id)
    assert seen == {o.order_id for o in book.open_orders()}, "order index out of sync"


@pytest.fixture
def book() -> OrderBook:
    return OrderBook()


@pytest.fixture
def assignment_book() -> OrderBook:
    """The 'book before' of the assignment example: four asks, no bids."""
    book = OrderBook()
    book.limit(Side.SELL, 101, 5)  # 1
    book.limit(Side.SELL, 101, 3)  # 2
    book.limit(Side.SELL, 102, 2)  # 3
    book.limit(Side.SELL, 103, 4)  # 4
    return book
