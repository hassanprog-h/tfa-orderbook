"""Command line: ``python -m orderbook demo`` or ``python -m orderbook replay journal.jsonl``."""

from __future__ import annotations

import argparse

from .book import OrderBook
from .journal import Journal, fingerprint, replay


def print_book(book: OrderBook, levels: int = 10) -> None:
    depth = book.depth(levels)
    print(f"{'':>10}  {'price':>8}  {'qty':>8}")
    for level in reversed(depth.asks):
        print(f"{'ASK':>10}  {level.price:>8}  {level.quantity:>8}")
    print(f"{'':>10}  {'-' * 8}  spread {book.spread()}")
    for level in depth.bids:
        print(f"{'BID':>10}  {level.price:>8}  {level.quantity:>8}")


def demo() -> None:
    """Replays the example of the assignment, step by step."""
    book = OrderBook()
    for price, quantity in [(101, 5), (101, 3), (102, 2), (103, 4)]:
        report = book.limit("sell", price, quantity)
        print(f"#{report.order_id} sell {quantity} @ {price}")
    print("\nBook before:")
    print_book(book)

    print("\nIncoming: buy 12 @ 102")
    report = book.limit("buy", 102, 12)
    for trade in report.trades:
        print(f"  trade {trade.quantity} @ {trade.price} against #{trade.sell_order_id}")
    print(
        f"  -> order #{report.order_id} is {report.status.value}, {report.remaining_quantity} rest"
    )

    print("\nBook after:")
    print_book(book)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m orderbook")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="run the example from the assignment")
    replay_cmd = commands.add_parser("replay", help="rebuild a book from a journal file")
    replay_cmd.add_argument("journal")
    replay_cmd.add_argument("--levels", type=int, default=10)
    args = parser.parse_args(argv)

    if args.command == "demo":
        demo()
    else:
        entries = Journal.load(args.journal)
        book = replay(entries)
        print(f"Replayed {len(entries)} journal entries: {len(book)} resting orders.")
        print(f"State fingerprint: {fingerprint(book)}\n")
        print_book(book, args.levels)


if __name__ == "__main__":
    main()
