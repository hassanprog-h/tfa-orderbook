"""Event sourcing: record every command sent to a book, and rebuild the book by replaying them.

The engine is fully deterministic (no clock, no randomness: ids and time priority come
from counters), so replaying the same commands always produces exactly the same book
and the same trades. This gives crash recovery, audit trails and reproducible bug
reports for free. :func:`fingerprint` hashes the full state so two books can be
compared in one line.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import IO, Any

from .book import OrderBook


class Journal:
    """Append-only list of commands, optionally mirrored to a JSON-lines file."""

    def __init__(self, path: str | Path | None = None) -> None:
        self.entries: list[dict[str, Any]] = []
        self._file: IO[str] | None = open(path, "w", encoding="utf-8") if path else None

    def start(self, book: OrderBook) -> None:
        self.record("config", {"self_trade_prevention": book.self_trade_prevention.value})

    def record(self, command: str, args: dict[str, Any]) -> None:
        entry = {"cmd": command, **args}
        self.entries.append(entry)
        if self._file is not None:
            self._file.write(json.dumps(entry) + "\n")
            self._file.flush()

    def close(self) -> None:
        if self._file is not None:
            self._file.close()
            self._file = None

    def to_jsonl(self) -> str:
        return "".join(json.dumps(entry) + "\n" for entry in self.entries)

    @staticmethod
    def load(path: str | Path) -> list[dict[str, Any]]:
        with open(path, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]


def replay(entries: Iterable[dict[str, Any]]) -> OrderBook:
    """Rebuild a book from journal entries."""
    book: OrderBook | None = None
    for entry in entries:
        args = dict(entry)
        command = args.pop("cmd")
        if command == "config":
            book = OrderBook(**args)
            continue
        if book is None:
            book = OrderBook()
        if command not in ("limit", "market", "cancel", "modify"):
            raise ValueError(f"unknown journal command {command!r}")
        getattr(book, command)(**args)
    return book if book is not None else OrderBook()


def fingerprint(book: OrderBook) -> str:
    """A short hash of the full book state (every order, in queue order, and the counters)."""
    state = {
        "book": book.snapshot(),
        "next_order_id": book._next_order_id,
        "next_trade_id": book._next_trade_id,
    }
    return hashlib.sha256(json.dumps(state, sort_keys=True).encode()).hexdigest()[:16]
