from conftest import check_invariants

from orderbook import OrderStatus


def test_cancel_resting_order(book):
    order_id = book.limit("buy", 100, 5).order_id
    assert book.cancel(order_id) is True
    assert book.get_order(order_id) is None
    assert book.best_bid() is None
    check_invariants(book)


def test_cancel_unknown_order_returns_false(book):
    assert book.cancel(42) is False


def test_cancel_twice(book):
    order_id = book.limit("buy", 100, 5).order_id
    assert book.cancel(order_id)
    assert not book.cancel(order_id)


def test_cancel_filled_order_returns_false(book):
    order_id = book.limit("sell", 100, 5).order_id
    book.limit("buy", 100, 5)
    assert book.cancel(order_id) is False


def test_cancel_middle_of_queue_keeps_others_in_order(book):
    a = book.limit("sell", 100, 1).order_id
    b = book.limit("sell", 100, 1).order_id
    c = book.limit("sell", 100, 1).order_id
    book.cancel(b)
    report = book.limit("buy", 100, 2)
    assert [t.sell_order_id for t in report.trades] == [a, c]


def test_cancel_partially_filled_order(book):
    order_id = book.limit("sell", 100, 10).order_id
    book.limit("buy", 100, 4)
    assert book.cancel(order_id)
    assert book.depth(1).asks == []


def test_cancelled_order_never_trades(book):
    order_id = book.limit("sell", 100, 5).order_id
    book.cancel(order_id)
    report = book.limit("buy", 100, 5)
    assert report.trades == [] and report.status is OrderStatus.NEW


def test_best_price_updates_after_cancelling_best_level(book):
    best = book.limit("buy", 101, 1).order_id
    book.limit("buy", 100, 1)
    book.cancel(best)
    assert book.best_bid() == 100


def test_level_recreated_after_being_emptied(book):
    order_id = book.limit("sell", 100, 1).order_id
    book.cancel(order_id)
    book.limit("sell", 105, 1)
    book.limit("sell", 100, 2)  # same price again
    assert book.best_ask() == 100
    book.cancel(3)
    assert book.best_ask() == 105
    check_invariants(book)


def test_depth_aggregates_and_orders_levels(book):
    for price, qty in [(99, 1), (100, 2), (100, 3), (98, 4), (97, 5)]:
        book.limit("buy", price, qty)
    for price, qty in [(101, 1), (103, 2), (102, 3), (102, 4)]:
        book.limit("sell", price, qty)

    depth = book.depth(3)
    assert [(lvl.price, lvl.quantity, lvl.orders) for lvl in depth.bids] == [
        (100, 5, 2),
        (99, 1, 1),
        (98, 4, 1),
    ]
    assert [(lvl.price, lvl.quantity, lvl.orders) for lvl in depth.asks] == [
        (101, 1, 1),
        (102, 7, 2),
        (103, 2, 1),
    ]


def test_depth_with_fewer_levels_than_requested(book):
    book.limit("buy", 100, 1)
    depth = book.depth(10)
    assert len(depth.bids) == 1 and depth.asks == []


def test_depth_zero(book):
    book.limit("buy", 100, 1)
    assert book.depth(0).bids == []


def test_empty_book_queries(book):
    assert book.best_bid() is None
    assert book.best_ask() is None
    assert book.spread() is None
    assert book.mid_price() is None
    assert book.depth(5).bids == [] and book.depth(5).asks == []


def test_spread_and_mid(book):
    book.limit("buy", 99, 1)
    book.limit("sell", 102, 1)
    assert book.spread() == 3
    assert book.mid_price() == 100.5


def test_snapshot_shows_queue_order(book):
    book.limit("buy", 100, 1)
    book.limit("buy", 100, 2)
    book.limit("sell", 101, 3)
    assert book.snapshot() == {
        "buy": [(100, [(1, 1), (2, 2)])],
        "sell": [(101, [(3, 3)])],
    }


def test_open_orders_by_trader(book):
    book.limit("buy", 100, 1, trader_id="alice")
    book.limit("buy", 99, 1, trader_id="bob")
    book.limit("sell", 105, 1, trader_id="alice")
    assert [o.order_id for o in book.open_orders("alice")] == [1, 3]
    assert len(book.open_orders()) == 3
