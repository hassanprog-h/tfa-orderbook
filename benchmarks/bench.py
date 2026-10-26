"""Throughput benchmark: ``python benchmarks/bench.py [n_orders]``.

Generates a realistic random flow (mostly passive limit orders around a moving price,
some aggressive orders and many cancels) and measures how many operations per second
the engine processes. The flow is generated up front so only the engine is timed.
"""

from __future__ import annotations

import random
import sys
import time

from orderbook import OrderBook


def generate(n: int, seed: int = 42) -> list[tuple]:
    rng = random.Random(seed)
    ops: list[tuple] = []
    mid, next_id, live = 10_000, 1, []
    for _ in range(n):
        mid += rng.choice((-1, 0, 1))
        r = rng.random()
        if r < 0.30 and live:  # cancel a random live order
            ops.append(("cancel", live.pop(rng.randrange(len(live)))))
            continue
        side = rng.choice(("buy", "sell"))
        if r < 0.40:  # market order
            ops.append(("market", side, rng.randint(1, 20)))
        else:  # limit order, mostly passive
            offset = rng.randint(-2, 30)
            price = mid - offset if side == "buy" else mid + offset
            ops.append(("limit", side, price, rng.randint(1, 50)))
            live.append(next_id)
        next_id += 1
    return ops


def run(ops: list[tuple]) -> tuple[float, int]:
    book = OrderBook()
    trades = 0
    start = time.perf_counter()
    for op in ops:
        if op[0] == "limit":
            trades += len(book.limit(op[1], op[2], op[3]).trades)
        elif op[0] == "market":
            trades += len(book.market(op[1], op[2]).trades)
        else:
            book.cancel(op[1])
    return time.perf_counter() - start, trades


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 200_000
    ops = generate(n)
    elapsed, trades = run(ops)
    print(f"{n:,} operations, {trades:,} trades in {elapsed:.2f}s")
    print(f"-> {n / elapsed:,.0f} operations/s, {elapsed / n * 1e6:.2f} µs per operation")


if __name__ == "__main__":
    main()
