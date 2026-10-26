import pytest

from orderbook import OrderBook, Side


@pytest.mark.parametrize("quantity", [0, -1])
def test_rejects_non_positive_quantity(book, quantity):
    with pytest.raises(ValueError):
        book.limit("buy", 100, quantity)


@pytest.mark.parametrize("price", [0, -5])
def test_rejects_non_positive_price(book, price):
    with pytest.raises(ValueError):
        book.limit("buy", price, 1)


@pytest.mark.parametrize("price", [100.5, 100.0, "100", None, True])
def test_rejects_non_integer_price(book, price):
    with pytest.raises(TypeError):
        book.limit("buy", price, 1)


@pytest.mark.parametrize("quantity", [1.5, "3", True])
def test_rejects_non_integer_quantity(book, quantity):
    with pytest.raises(TypeError):
        book.limit("buy", 100, quantity)


def test_rejects_unknown_side(book):
    with pytest.raises(ValueError):
        book.limit("hold", 100, 1)


def test_side_accepts_strings_and_enum(book):
    book.limit("BUY", 100, 1)
    book.limit(Side.BUY, 100, 1)
    book.limit("buy", 100, 1)
    assert book.depth(1).bids[0].quantity == 3


def test_invalid_order_does_not_consume_an_id(book):
    with pytest.raises(ValueError):
        book.limit("buy", 100, 0)
    assert book.limit("buy", 100, 1).order_id == 1


def test_market_order_cannot_be_gtc(book):
    with pytest.raises(ValueError):
        book.market("buy", 1, tif="gtc")


def test_post_only_must_be_gtc(book):
    with pytest.raises(ValueError):
        book.limit("buy", 100, 1, post_only=True, tif="ioc")


def test_rejects_unknown_tif(book):
    with pytest.raises(ValueError):
        book.limit("buy", 100, 1, tif="forever")


def test_rejects_unknown_stp_policy():
    with pytest.raises(ValueError):
        OrderBook(self_trade_prevention="sometimes")
