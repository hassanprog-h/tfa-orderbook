"""Property-based tests: thousands of random order sequences, checked two ways.

1. Differential testing: every sequence is also run through ``ReferenceBook``, a naive
   implementation that is obviously correct (it scans every resting order to find the
   best one) but slow. Both must produce exactly the same trades and the same book.
2. Invariants: after every step the book is never crossed, quantities add up, etc.
"""

from __future__ import annotations

from dataclasses import dataclass

from conftest import check_invariants
from hypothesis import given, settings
from hypothesis import strategies as st

from orderbook import Journal, OrderBook, fingerprint, replay


@dataclass
class RefOrder:
    order_id: int
    side: str
    price: int
    remaining: int
    sequence: int


class ReferenceBook:
    """Deliberately simple: a flat list, searched linearly on every match."""

    def __init__(self) -> None:
        self.resting: list[RefOrder] = []
        self.next_id = 1
        self.sequence = 0

    def _crossing(self, side: str, price: int | None) -> list[RefOrder]:
        def ok(o: RefOrder) -> bool:
            if o.side == side:
                return False
            if price is None:
                return True
            return o.price <= price if side == "buy" else o.price >= price

        # Price priority, then time priority.
        def priority(o: RefOrder):
            return (o.price if side == "buy" else -o.price, o.sequence)

        return sorted(filter(ok, self.resting), key=priority)

    def _submit(self, order_id, side, price, qty, tif):
        self.sequence += 1
        if tif == "fok" and sum(o.remaining for o in self._crossing(side, price)) < qty:
            return []
        trades = []
        for resting in self._crossing(side, price):
            if qty == 0:
                break
            q = min(qty, resting.remaining)
            pair = (order_id, resting.order_id) if side == "buy" else (resting.order_id, order_id)
            trades.append((*pair, resting.price, q))
            resting.remaining -= q
            qty -= q
        self.resting = [o for o in self.resting if o.remaining > 0]
        if qty > 0 and price is not None and tif == "gtc":
            self.resting.append(RefOrder(order_id, side, price, qty, self.sequence))
        return trades

    def limit(self, side, price, qty, tif):
        order_id = self.next_id
        self.next_id += 1
        return self._submit(order_id, side, price, qty, tif)

    def market(self, side, qty):
        order_id = self.next_id
        self.next_id += 1
        return self._submit(order_id, side, None, qty, "ioc")

    def cancel(self, order_id):
        self.resting = [o for o in self.resting if o.order_id != order_id]
        return []

    def modify(self, order_id, price, qty):
        order = next((o for o in self.resting if o.order_id == order_id), None)
        if order is None:
            return []
        new_price = order.price if price is None else price
        new_qty = order.remaining if qty is None else qty
        if new_price == order.price and new_qty <= order.remaining:
            order.remaining = new_qty
            return []
        self.resting.remove(order)
        return self._submit(order_id, order.side, new_price, new_qty, "gtc")

    def depth(self, side: str, n: int):
        levels: dict[int, int] = {}
        for o in self.resting:
            if o.side == side:
                levels[o.price] = levels.get(o.price, 0) + o.remaining
        prices = sorted(levels, reverse=(side == "buy"))[:n]
        return [(p, levels[p]) for p in prices]


sides = st.sampled_from(["buy", "sell"])
prices = st.integers(min_value=95, max_value=105)  # narrow range: lots of crossing
quantities = st.integers(min_value=1, max_value=10)
order_ids = st.integers(min_value=1, max_value=60)

operations = st.lists(
    st.one_of(
        st.tuples(
            st.just("limit"),
            sides,
            prices,
            quantities,
            st.sampled_from(["gtc", "gtc", "gtc", "ioc", "fok"]),
        ),
        st.tuples(st.just("market"), sides, quantities),
        st.tuples(st.just("cancel"), order_ids),
        st.tuples(st.just("modify"), order_ids, st.none() | prices, st.none() | quantities),
    ),
    max_size=80,
)


def run(book: OrderBook, op) -> list[tuple[int, int, int, int]]:
    kind, *args = op
    if kind == "limit":
        side, price, qty, tif = args
        trades = book.limit(side, price, qty, tif=tif).trades
    elif kind == "market":
        trades = book.market(args[0], args[1]).trades
    elif kind == "cancel":
        book.cancel(args[0])
        trades = []
    else:
        order_id, price, qty = args
        trades = book.modify(order_id, price=price, quantity=qty).trades
    return [(t.buy_order_id, t.sell_order_id, t.price, t.quantity) for t in trades]


def run_reference(ref: ReferenceBook, op):
    kind, *args = op
    if kind == "limit":
        return ref.limit(*args)
    if kind == "market":
        return ref.market(*args)
    if kind == "cancel":
        return ref.cancel(*args)
    return ref.modify(*args)


@settings(max_examples=400, deadline=None)
@given(operations)
def test_engine_matches_reference_model(ops):
    book, ref = OrderBook(), ReferenceBook()
    for op in ops:
        assert run(book, op) == run_reference(ref, op), op
        depth = book.depth(20)
        assert [(lvl.price, lvl.quantity) for lvl in depth.bids] == ref.depth("buy", 20)
        assert [(lvl.price, lvl.quantity) for lvl in depth.asks] == ref.depth("sell", 20)
        check_invariants(book)


@settings(max_examples=100, deadline=None)
@given(operations)
def test_replaying_the_journal_rebuilds_the_same_book(ops):
    journal = Journal()
    book = OrderBook(journal=journal)
    trades = [run(book, op) for op in ops]

    rebuilt = replay(journal.entries)
    assert fingerprint(rebuilt) == fingerprint(book)
    assert rebuilt.snapshot() == book.snapshot()

    # And replaying produces the very same trades.
    replayed = OrderBook(journal=Journal())
    assert [run(replayed, op) for op in ops] == trades
