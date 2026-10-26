"""Conversion between human prices (``Decimal("101.25")``) and integer ticks."""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal


@dataclass(frozen=True)
class Instrument:
    """The tradable thing: a symbol and its tick size (smallest price increment).

    >>> eth = Instrument("ETH-USD", Decimal("0.05"))
    >>> eth.to_ticks("101.25")
    2025
    >>> eth.to_price(2025)
    Decimal('101.25')
    """

    symbol: str
    tick_size: Decimal = Decimal("0.01")

    def __post_init__(self) -> None:
        object.__setattr__(self, "tick_size", Decimal(str(self.tick_size)))
        if self.tick_size <= 0:
            raise ValueError("tick size must be positive")

    def to_ticks(self, price: Decimal | str | int | float) -> int:
        """Convert a price to ticks; refuse prices that are not on the tick grid.

        Floats are converted through ``str`` so that ``0.1`` means ``Decimal("0.1")``
        and not ``0.1000000000000000055511151231257827``.
        """
        value = Decimal(str(price))
        ticks, rest = divmod(value, self.tick_size)
        if rest != 0:
            raise ValueError(f"price {value} is not a multiple of the tick size {self.tick_size}")
        return int(ticks)

    def to_price(self, ticks: int) -> Decimal:
        return ticks * self.tick_size
