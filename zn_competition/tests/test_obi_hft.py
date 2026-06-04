"""Tests for Level 1 direct OBI HFT execution in volume_aware_mm."""

from __future__ import annotations

import unittest

from zn_competition.economics import net_pnl_from_tick_move
from zn_competition.microstructure import (
    OrderBookSnapshot,
    Quote,
    calculate_order_book_imbalance,
)
from zn_competition.risk import PositionLedger
from zn_competition.specs import FEE_PER_LOT_ROUND_TURN_USD, MAX_POSITION_LOTS
from zn_competition.strategies.volume_aware_mm import (
    OBI_ENTRY_THRESHOLD,
    OrderBookImbalanceHFT,
    scratch_net_pnl_usd,
)


def _bullish_book() -> OrderBookSnapshot:
    return OrderBookSnapshot(
        timestamp="2026-06-03T14:00:00+00:00",
        bid=112.0,
        ask=112.03125,
        direct_bid_qty=110,
        direct_ask_qty=10,
        bid_order_count=20,
        ask_order_count=5,
    )


def _bearish_flip_book() -> OrderBookSnapshot:
    return OrderBookSnapshot(
        timestamp="2026-06-03T14:00:01+00:00",
        bid=112.0,
        ask=112.03125,
        direct_bid_qty=10,
        direct_ask_qty=110,
        bid_order_count=5,
        ask_order_count=20,
    )


def _quote_from_book(book: OrderBookSnapshot) -> Quote:
    return Quote(
        book.timestamp,
        book.bid,
        book.ask,
        direct_bid_qty=book.direct_bid_qty,
        direct_ask_qty=book.direct_ask_qty,
        bid_order_count=book.bid_order_count,
        ask_order_count=book.ask_order_count,
    )


class TestOrderBookImbalance(unittest.TestCase):
    def test_direct_obi_formula(self) -> None:
        book = _bullish_book()
        obi = calculate_order_book_imbalance(book)
        self.assertAlmostEqual(obi, (110 - 10) / 120, places=6)
        self.assertGreater(obi, OBI_ENTRY_THRESHOLD)

    def test_position_cap_rejects_oversized_entry(self) -> None:
        engine = OrderBookImbalanceHFT(quote_size=5)
        ledger = PositionLedger(position=8)
        book = _bullish_book()
        quote = _quote_from_book(book)
        engine.process_tick(quote, book, ledger)
        self.assertLessEqual(abs(ledger.position), MAX_POSITION_LOTS)


class TestOBIExecution(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = OrderBookImbalanceHFT(quote_size=1)
        self.ledger = PositionLedger()

    def test_passive_bid_entry_at_inside(self) -> None:
        book = _bullish_book()
        quote = _quote_from_book(book)
        result = self.engine.process_tick(quote, book, self.ledger)
        self.assertEqual(self.ledger.position, 1)
        self.assertEqual(self.ledger.avg_entry_price, book.inside_bid)
        self.assertGreater(len(result.fills), 0)
        self.assertIn(result.action, ("enter_passive_bid", "fill_passive"))

    def test_scratch_on_book_flip_costs_one_dollar_rt(self) -> None:
        book = _bullish_book()
        quote = _quote_from_book(book)
        self.engine.process_tick(quote, book, self.ledger)
        self.assertEqual(self.ledger.position, 1)

        flip_book = _bearish_flip_book()
        flip_quote = _quote_from_book(flip_book)
        scratch = self.engine.process_tick(flip_quote, flip_book, self.ledger)
        self.assertEqual(scratch.action, "scratch")
        self.assertEqual(self.ledger.position, 0)
        self.assertAlmostEqual(scratch.scratch_pnl_usd, scratch_net_pnl_usd(1))
        self.assertAlmostEqual(scratch.scratch_pnl_usd, -FEE_PER_LOT_ROUND_TURN_USD)
        self.assertAlmostEqual(self.ledger.realized_pnl_usd, -FEE_PER_LOT_ROUND_TURN_USD, places=4)

    def test_scratch_matches_economics_util(self) -> None:
        self.assertAlmostEqual(
            scratch_net_pnl_usd(3),
            net_pnl_from_tick_move(0.0, 3, sides=2).net_pnl_usd,
        )


if __name__ == "__main__":
    unittest.main()
