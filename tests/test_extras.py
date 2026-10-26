"""Instrument (ticks), journal / replay and the trader ledger."""

from decimal import Decimal

import pytest

from orderbook import (
    Instrument,
    Journal,
    Ledger,
    OrderBook,
    SelfTradePrevention,
    fingerprint,
    replay,
)

# ---------------------------------------------------------------- instrument


def test_to_ticks_is_exact_where_floats_are_not():
    assert 0.1 + 0.2 != 0.3  # the classic trap
    inst = Instrument("TEST", Decimal("0.1"))
    assert inst.to_ticks(0.1) + inst.to_ticks(0.2) == inst.to_ticks(0.3)


def test_to_ticks_and_back():
    inst = Instrument("TEST", Decimal("0.05"))
    assert inst.to_ticks("101.25") == 2025
    assert inst.to_ticks(101.25) == 2025
    assert inst.to_price(2025) == Decimal("101.25")


def test_off_grid_price_is_rejected():
    inst = Instrument("TEST", Decimal("0.05"))
    with pytest.raises(ValueError):
        inst.to_ticks("101.26")


def test_tick_size_must_be_positive():
    with pytest.raises(ValueError):
        Instrument("TEST", Decimal("0"))


# ---------------------------------------------------------------- journal


def test_journal_records_only_valid_commands():
    journal = Journal()
    book = OrderBook(journal=journal)
    book.limit("buy", 100, 1)
    with pytest.raises(ValueError):
        book.limit("buy", 100, 0)
    book.cancel(1)
    assert [e["cmd"] for e in journal.entries] == ["config", "limit", "cancel"]


def test_journal_file_roundtrip(tmp_path):
    path = tmp_path / "journal.jsonl"
    journal = Journal(path)
    book = OrderBook(SelfTradePrevention.CANCEL_RESTING, journal=journal)
    book.limit("sell", 101, 5, trader_id="alice")
    book.limit("sell", 102, 5, trader_id="bob")
    book.limit("buy", 102, 7, trader_id="alice")
    book.modify(3, quantity=1)
    book.market("sell", 1)
    journal.close()

    rebuilt = replay(Journal.load(path))
    assert rebuilt.self_trade_prevention is SelfTradePrevention.CANCEL_RESTING
    assert fingerprint(rebuilt) == fingerprint(book)


def test_fingerprint_changes_with_state():
    a, b = OrderBook(), OrderBook()
    a.limit("buy", 100, 1)
    b.limit("buy", 100, 2)
    assert fingerprint(a) != fingerprint(b)


def test_replay_rejects_unknown_command():
    with pytest.raises(ValueError):
        replay([{"cmd": "explode"}])


# ---------------------------------------------------------------- ledger


def test_ledger_positions_cash_and_pnl():
    book, ledger = OrderBook(), Ledger()
    book.subscribe(ledger.on_event)
    book.limit("sell", 100, 10, trader_id="alice")
    book.limit("buy", 100, 4, trader_id="bob")
    book.limit("buy", 110, 6, trader_id="carol")  # trades 6 @ 100

    alice, bob = ledger.account("alice"), ledger.account("bob")
    assert (alice.position, alice.cash) == (-10, 1000)
    assert (bob.position, bob.cash) == (4, -400)
    assert bob.average_price() == 100

    # Price moves to 120: buyers win, the seller loses.
    book.limit("sell", 120, 1, trader_id="dave")
    book.limit("buy", 120, 1, trader_id="erin")
    mark = book.last_trade.price
    assert bob.pnl(mark) == 4 * 20
    assert alice.pnl(mark) == -10 * 20
    # Zero-sum: PnLs add up to zero.
    assert sum(a.pnl(mark) for a in ledger.accounts.values()) == 0
    assert ledger.leaderboard(mark)[0].trader_id == "carol"


def test_ledger_ignores_anonymous_side():
    book, ledger = OrderBook(), Ledger()
    book.subscribe(ledger.on_event)
    book.limit("sell", 100, 1)
    book.limit("buy", 100, 1, trader_id="bob")
    assert list(ledger.accounts) == ["bob"]
