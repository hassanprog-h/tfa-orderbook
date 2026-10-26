"""Extensions: market orders, IOC, FOK, post-only, modify, self-trade prevention."""

import pytest
from conftest import check_invariants

from orderbook import OrderBook, OrderStatus, SelfTradePrevention

# ---------------------------------------------------------------- market orders


def test_market_buy_sweeps_best_prices(assignment_book):
    report = assignment_book.market("buy", 9)
    assert [(t.price, t.quantity) for t in report.trades] == [(101, 5), (101, 3), (102, 1)]
    assert report.status is OrderStatus.FILLED


def test_market_order_never_rests(book):
    book.limit("sell", 100, 2)
    report = book.market("buy", 5)
    assert report.filled_quantity == 2
    assert report.status is OrderStatus.CANCELLED
    assert report.remaining_quantity == 0
    assert book.best_bid() is None


def test_market_order_on_empty_book(book):
    report = book.market("sell", 5)
    assert report.trades == [] and report.status is OrderStatus.CANCELLED


def test_market_fok(book):
    book.limit("sell", 100, 2)
    report = book.market("buy", 5, tif="fok")
    assert report.trades == [] and report.status is OrderStatus.CANCELLED
    assert book.depth(1).asks[0].quantity == 2  # untouched


# ---------------------------------------------------------------- IOC / FOK


def test_ioc_fills_what_it_can_and_cancels_the_rest(book):
    book.limit("sell", 100, 3)
    book.limit("sell", 102, 3)
    report = book.limit("buy", 101, 5, tif="ioc")
    assert report.filled_quantity == 3
    assert report.status is OrderStatus.CANCELLED
    assert book.best_bid() is None and book.best_ask() == 102


def test_ioc_fully_filled(book):
    book.limit("sell", 100, 5)
    assert book.limit("buy", 100, 5, tif="ioc").status is OrderStatus.FILLED


def test_fok_rejected_when_not_enough_liquidity(book):
    book.limit("sell", 100, 3)
    book.limit("sell", 101, 1)
    report = book.limit("buy", 101, 5, tif="fok")
    assert report.status is OrderStatus.CANCELLED and report.trades == []
    assert len(book) == 2  # nothing touched


def test_fok_ignores_liquidity_beyond_its_limit(book):
    book.limit("sell", 100, 3)
    book.limit("sell", 105, 10)
    assert book.limit("buy", 101, 5, tif="fok").trades == []


def test_fok_filled_across_levels(book):
    book.limit("sell", 100, 3)
    book.limit("sell", 101, 2)
    report = book.limit("buy", 101, 5, tif="fok")
    assert report.status is OrderStatus.FILLED and len(report.trades) == 2


# ---------------------------------------------------------------- post-only


def test_post_only_rests_when_not_crossing(book):
    book.limit("sell", 101, 1)
    report = book.limit("buy", 100, 1, post_only=True)
    assert report.status is OrderStatus.NEW and book.best_bid() == 100


def test_post_only_rejected_when_crossing(book):
    book.limit("sell", 100, 1)
    report = book.limit("buy", 100, 1, post_only=True)
    assert report.status is OrderStatus.REJECTED and report.trades == []
    assert book.best_bid() is None and book.best_ask() == 100


# ---------------------------------------------------------------- modify


def test_reducing_quantity_keeps_time_priority(book):
    first = book.limit("sell", 100, 5).order_id
    second = book.limit("sell", 100, 5).order_id
    report = book.modify(first, quantity=2)
    assert report.status is OrderStatus.NEW and report.remaining_quantity == 2
    trades = book.limit("buy", 100, 3).trades
    assert [(t.sell_order_id, t.quantity) for t in trades] == [(first, 2), (second, 1)]
    check_invariants(book)


def test_increasing_quantity_loses_time_priority(book):
    first = book.limit("sell", 100, 5).order_id
    second = book.limit("sell", 100, 5).order_id
    book.modify(first, quantity=6)
    trades = book.limit("buy", 100, 5).trades
    assert [t.sell_order_id for t in trades] == [second]
    assert book.get_order(first).remaining == 6
    check_invariants(book)


def test_price_change_moves_order_and_keeps_id(book):
    order_id = book.limit("buy", 99, 5).order_id
    book.modify(order_id, price=98)
    assert book.best_bid() == 98
    assert book.get_order(order_id).price == 98


def test_modify_to_crossing_price_trades(book):
    book.limit("sell", 101, 3)
    order_id = book.limit("buy", 99, 5).order_id
    report = book.modify(order_id, price=101)
    assert report.order_id == order_id
    assert [(t.price, t.quantity) for t in report.trades] == [(101, 3)]
    assert book.get_order(order_id).remaining == 2
    check_invariants(book)


def test_modify_partially_filled_order_keeps_filled_amount(book):
    order_id = book.limit("sell", 100, 10).order_id
    book.limit("buy", 100, 4)  # 6 left
    book.modify(order_id, quantity=3)
    order = book.get_order(order_id)
    assert (order.filled, order.remaining, order.quantity) == (4, 3, 7)


def test_modify_unknown_order(book):
    assert book.modify(99, quantity=1).status is OrderStatus.REJECTED


def test_modify_validates_input(book):
    order_id = book.limit("buy", 100, 5).order_id
    with pytest.raises(ValueError):
        book.modify(order_id, quantity=0)


# ---------------------------------------------------------------- self-trade prevention


def test_self_trade_allowed_by_default(book):
    book.limit("sell", 100, 1, trader_id="alice")
    assert len(book.limit("buy", 100, 1, trader_id="alice").trades) == 1


def test_stp_cancel_resting():
    book = OrderBook(SelfTradePrevention.CANCEL_RESTING)
    own = book.limit("sell", 100, 1, trader_id="alice").order_id
    other = book.limit("sell", 100, 1, trader_id="bob").order_id
    report = book.limit("buy", 100, 2, trader_id="alice")
    assert [t.sell_order_id for t in report.trades] == [other]
    assert book.get_order(own) is None  # alice's resting order was cancelled
    assert book.best_bid() == 100  # the rest of alice's buy rests
    check_invariants(book)


def test_stp_cancel_incoming():
    book = OrderBook(SelfTradePrevention.CANCEL_INCOMING)
    book.limit("sell", 100, 1, trader_id="bob")
    own = book.limit("sell", 100, 1, trader_id="alice").order_id
    report = book.limit("buy", 100, 5, trader_id="alice")
    assert len(report.trades) == 1  # traded with bob, then stopped at its own order
    assert report.status is OrderStatus.CANCELLED
    assert book.get_order(own) is not None and book.best_bid() is None


def test_stp_cancel_both():
    book = OrderBook(SelfTradePrevention.CANCEL_BOTH)
    book.limit("sell", 100, 1, trader_id="alice")
    report = book.limit("buy", 100, 1, trader_id="alice")
    assert report.trades == [] and report.status is OrderStatus.CANCELLED
    assert len(book) == 0


def test_stp_ignores_anonymous_orders():
    book = OrderBook(SelfTradePrevention.CANCEL_BOTH)
    book.limit("sell", 100, 1)
    assert len(book.limit("buy", 100, 1).trades) == 1


@pytest.mark.parametrize(
    "policy, expected",
    [
        (SelfTradePrevention.CANCEL_RESTING, OrderStatus.FILLED),  # own order skipped
        (SelfTradePrevention.CANCEL_INCOMING, OrderStatus.CANCELLED),  # stops at own order
    ],
)
def test_fok_takes_stp_into_account(policy, expected):
    book = OrderBook(policy)
    book.limit("sell", 100, 2, trader_id="alice")
    book.limit("sell", 100, 3, trader_id="bob")
    report = book.limit("buy", 100, 3, trader_id="alice", tif="fok")
    assert report.status is expected
    check_invariants(book)
