"""The required core: limit orders, price-time priority, partial fills, resting price."""

from conftest import check_invariants

from orderbook import OrderBook, OrderStatus, Side


def fills(report):
    return [(t.buy_order_id, t.sell_order_id, t.price, t.quantity) for t in report.trades]


def test_assignment_example(assignment_book):
    report = assignment_book.limit(Side.BUY, 102, 12)

    assert report.order_id == 5
    assert fills(report) == [(5, 1, 101, 5), (5, 2, 101, 3), (5, 3, 102, 2)]
    assert report.status is OrderStatus.PARTIALLY_FILLED
    assert report.filled_quantity == 10 and report.remaining_quantity == 2
    # Book after: #5 rests with 2 @ 102 (new best bid), #4 untouched (best ask 103).
    assert assignment_book.best_bid() == 102
    assert assignment_book.best_ask() == 103
    depth = assignment_book.depth(5)
    assert [(lvl.price, lvl.quantity) for lvl in depth.bids] == [(102, 2)]
    assert [(lvl.price, lvl.quantity) for lvl in depth.asks] == [(103, 4)]
    check_invariants(assignment_book)


def test_order_ids_are_unique_and_increasing(book):
    ids = [book.limit("buy", 100 - i, 1).order_id for i in range(5)]
    assert ids == [1, 2, 3, 4, 5]


def test_non_crossing_orders_rest(book):
    book.limit("buy", 99, 10)
    report = book.limit("sell", 100, 10)
    assert report.trades == []
    assert report.status is OrderStatus.NEW
    assert (book.best_bid(), book.best_ask()) == (99, 100)
    check_invariants(book)


def test_buy_at_exactly_best_ask_crosses(book):
    book.limit("sell", 100, 5)
    assert fills(book.limit("buy", 100, 5)) == [(2, 1, 100, 5)]
    assert len(book) == 0


def test_sell_at_exactly_best_bid_crosses(book):
    book.limit("buy", 100, 5)
    assert fills(book.limit("sell", 100, 5)) == [(1, 2, 100, 5)]
    assert len(book) == 0


def test_price_priority_buy_takes_lowest_ask_first(book):
    book.limit("sell", 103, 1)
    book.limit("sell", 101, 1)
    book.limit("sell", 102, 1)
    report = book.limit("buy", 103, 3)
    assert [t.price for t in report.trades] == [101, 102, 103]


def test_price_priority_sell_takes_highest_bid_first(book):
    book.limit("buy", 97, 1)
    book.limit("buy", 99, 1)
    book.limit("buy", 98, 1)
    report = book.limit("sell", 97, 3)
    assert [t.price for t in report.trades] == [99, 98, 97]


def test_time_priority_at_equal_price(book):
    first = book.limit("buy", 100, 2).order_id
    second = book.limit("buy", 100, 2).order_id
    third = book.limit("buy", 100, 2).order_id
    report = book.limit("sell", 100, 5)
    assert [(t.buy_order_id, t.quantity) for t in report.trades] == [
        (first, 2),
        (second, 2),
        (third, 1),
    ]
    # The partially filled third order keeps its place (it is now first in line).
    assert book.get_order(third).remaining == 1
    assert book.get_order(third).status is OrderStatus.PARTIALLY_FILLED


def test_trade_executes_at_resting_price_not_incoming_price(book):
    book.limit("sell", 100, 5)
    report = book.limit("buy", 110, 5)  # willing to pay 110
    assert report.trades[0].price == 100  # but pays the resting 100

    book.limit("buy", 90, 5)
    report = book.limit("sell", 80, 5)
    assert report.trades[0].price == 90


def test_incoming_partially_filled_rests_remainder(book):
    book.limit("sell", 100, 3)
    report = book.limit("buy", 100, 10)
    assert report.filled_quantity == 3 and report.remaining_quantity == 7
    assert book.best_bid() == 100 and book.best_ask() is None
    assert book.depth(1).bids[0].quantity == 7


def test_resting_partially_filled_stays_with_remainder(book):
    resting = book.limit("sell", 100, 10).order_id
    report = book.limit("buy", 100, 4)
    assert report.status is OrderStatus.FILLED
    assert book.get_order(resting).remaining == 6
    assert book.depth(1).asks[0].quantity == 6


def test_matching_stops_when_prices_no_longer_cross(book):
    book.limit("sell", 100, 1)
    book.limit("sell", 105, 1)
    report = book.limit("buy", 102, 5)
    assert [t.price for t in report.trades] == [100]
    assert book.best_bid() == 102 and book.best_ask() == 105
    check_invariants(book)


def test_sweep_through_many_levels(book):
    for price in range(101, 111):
        book.limit("sell", price, 1)
    report = book.limit("buy", 200, 10)
    assert [t.price for t in report.trades] == list(range(101, 111))
    assert book.best_ask() is None
    assert report.status is OrderStatus.FILLED
    assert report.average_price == 105.5


def test_trade_reports_aggressor_and_ids(book):
    sell = book.limit("sell", 100, 1).order_id
    report = book.limit("buy", 100, 1)
    trade = report.trades[0]
    assert (trade.buy_order_id, trade.sell_order_id) == (report.order_id, sell)
    assert trade.aggressor is Side.BUY


def test_trade_ids_increase_across_orders(book):
    book.limit("sell", 100, 2)
    a = book.limit("buy", 100, 1).trades[0].trade_id
    b = book.limit("buy", 100, 1).trades[0].trade_id
    assert b == a + 1


def test_events_are_published(book):
    events = []
    book.subscribe(events.append)
    book.limit("sell", 100, 2)
    book.limit("buy", 100, 3)
    trades = [e for e in events if e.__class__.__name__ == "Trade"]
    updates = [(e.order_id, e.status) for e in events if e.__class__.__name__ == "OrderUpdate"]
    assert len(trades) == 1 and trades[0].quantity == 2
    assert updates == [
        (1, OrderStatus.NEW),
        (1, OrderStatus.FILLED),
        (2, OrderStatus.PARTIALLY_FILLED),
    ]


def test_unsubscribe(book):
    events = []
    unsubscribe = book.subscribe(events.append)
    unsubscribe()
    book.limit("buy", 100, 1)
    assert events == []


def test_last_trade(book):
    assert book.last_trade is None
    book.limit("sell", 100, 1)
    book.limit("buy", 100, 1)
    assert book.last_trade.price == 100


def test_large_quantities_are_exact():
    book = OrderBook()
    big = 10**15
    book.limit("sell", 10**9, big)
    report = book.limit("buy", 10**9, big - 1)
    assert report.filled_quantity == big - 1
    assert book.depth(1).asks[0].quantity == 1
