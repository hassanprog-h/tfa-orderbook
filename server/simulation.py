"""Simulated market participants, so that the UI has a live market to trade against.

* ``mm-alpha`` is a market maker: it always quotes a few levels on both sides around
  its estimate of the fair value, and skews its quotes against its inventory (when it
  is long it lowers its prices to sell more and buy less).
* ``noise-*`` traders send random limit and market orders around the fair value.
* ``trend-1`` follows momentum: it buys after the price went up and sells after it
  went down, which creates small trends.

The fair value follows a random walk and is pulled towards the last traded price, so
a human who trades aggressively really moves the market.
"""

from __future__ import annotations

import random
from collections import deque

from .exchange import Exchange

MARKET_MAKER = "mm-alpha"
NOISE_TRADERS = ("noise-1", "noise-2", "noise-3")
TREND_FOLLOWER = "trend-1"
BOTS = (MARKET_MAKER, *NOISE_TRADERS, TREND_FOLLOWER)


class Simulation:
    def __init__(self, exchange: Exchange, seed: int | None = None, start_price: int = 10_000):
        self.exchange = exchange
        self.rng = random.Random(seed)
        self.start_price = start_price
        self.running = True
        self.speed = 1.0
        exchange.bots.update(BOTS)
        self.reset()

    def reset(self) -> None:
        self.fair = float(self.start_price)  # in ticks
        self.steps = 0
        self.order_birth: dict[int, int] = {}  # bot order id -> step it was placed
        self.recent_prices: deque[int] = deque(maxlen=30)

    @property
    def book(self):
        return self.exchange.book

    def step(self) -> None:
        self.steps += 1
        last = self.book.last_trade.price if self.book.last_trade else None
        if last is not None:
            self.recent_prices.append(last)
            self.fair += 0.15 * (last - self.fair)  # pulled towards where people trade
        self.fair += self.rng.gauss(0, 2.5)
        self.fair = max(self.fair, 50)

        if self.steps % 3 == 1 or self._quotes_missing():
            self._market_maker()
        for trader in NOISE_TRADERS:
            if self.rng.random() < 0.7:
                self._noise(trader)
        if self.rng.random() < 0.25:
            self._trend()
        self._expire_old_orders(max_age=60)
        self.exchange.touch()

    # ---------------------------------------------------------------- bots

    def _quotes_missing(self) -> bool:
        own = self.book.open_orders(MARKET_MAKER)
        return not any(o.side.value == "buy" for o in own) or not any(
            o.side.value == "sell" for o in own
        )

    def _market_maker(self) -> None:
        for order in self.book.open_orders(MARKET_MAKER):
            self.book.cancel(order.order_id)
        position = self.exchange.ledger.account(MARKET_MAKER).position
        center = self.fair - 0.08 * position  # inventory skew
        half_spread = self.rng.randint(2, 4)
        for i in range(6):
            size = self.rng.randint(5, 25) * (i + 1)
            bid = round(center) - half_spread - 2 * i
            ask = round(center) + half_spread + 2 * i
            self._place(MARKET_MAKER, "buy", bid, size, post_only=True)
            self._place(MARKET_MAKER, "sell", ask, size, post_only=True)

    def _noise(self, trader: str) -> None:
        side = self.rng.choice(("buy", "sell"))
        if self.rng.random() < 0.25:
            self.book.market(side, self.rng.randint(1, 12), trader_id=trader)
            return
        offset = round(abs(self.rng.gauss(0, 8))) - 2  # mostly passive, sometimes crossing
        price = round(self.fair) - offset if side == "buy" else round(self.fair) + offset
        self._place(trader, side, price, self.rng.randint(1, 30))

    def _trend(self) -> None:
        if len(self.recent_prices) < self.recent_prices.maxlen:
            return
        move = self.recent_prices[-1] - self.recent_prices[0]
        position = self.exchange.ledger.account(TREND_FOLLOWER).position
        if abs(move) >= 4 and position * move < 300 * abs(move):  # position limit of 300
            self.book.market(
                "buy" if move > 0 else "sell", self.rng.randint(5, 20), trader_id=TREND_FOLLOWER
            )

    def _place(self, trader: str, side: str, price: int, quantity: int, post_only=False) -> None:
        if price <= 0:
            return
        report = self.book.limit(side, price, quantity, trader_id=trader, post_only=post_only)
        if report.status.is_open:
            self.order_birth[report.order_id] = self.steps

    def _expire_old_orders(self, max_age: int) -> None:
        for order_id, born in list(self.order_birth.items()):
            if self.book.get_order(order_id) is None:
                del self.order_birth[order_id]
            elif self.steps - born > max_age:
                self.book.cancel(order_id)
                del self.order_birth[order_id]
