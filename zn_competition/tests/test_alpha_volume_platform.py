"""Alpha Sniper + Module 4 Volume Churner platform tests."""

from __future__ import annotations

import unittest

from zn_competition.risk import PositionLedger
from zn_competition.strategies.alpha_volume_platform import (
    SNIPER_OBI_THRESHOLD_DEFAULT,
    AlphaVolumePlatform,
)
from zn_competition.strategies.base import StrategyContext
from zn_competition.strategies.session_mr import CHURN_GENERATOR_PERIOD_MS
from zn_competition.strategies.volume_aware_mm import SniperOBIEngine
from zn_competition.strategies.volume_aware_mm import OpenOBITrade
from zn_competition.tests.level1_fixtures import BALANCED_L1, BULLISH_L1, l1_book, l1_quote


def _flat_ctx(book, position: int = 0) -> StrategyContext:
    return StrategyContext(
        mid_price=(book.bid + book.ask) / 2,
        bid=book.bid,
        ask=book.ask,
        position=position,
        week_number=1,
        leg_lots_traded_this_week=0,
        leg_lots_traded_total=0,
        weekly_min_remaining=200,
        book=book,
        direct_bid_qty=book.direct_bid_qty,
        direct_ask_qty=book.direct_ask_qty,
    )


class TestSniperOBIEngine(unittest.TestCase):
    def test_24_7_allows_entries(self) -> None:
        engine = SniperOBIEngine(entry_threshold=0.85)
        self.assertTrue(engine._allows_new_entries())
        self.assertAlmostEqual(engine.entry_threshold, SNIPER_OBI_THRESHOLD_DEFAULT)


class TestAlphaVolumePlatform(unittest.TestCase):
    def test_obi_blocks_churn_when_in_trade(self) -> None:
        platform = AlphaVolumePlatform.with_sniper_threshold(0.85)
        book = l1_book(BULLISH_L1)
        quote = l1_quote(BULLISH_L1)
        ledger = PositionLedger()
        ctx = _flat_ctx(book)

        from zn_competition.strategies.base import Side

        platform.alpha._open_trade = OpenOBITrade(
            side=Side.BUY,
            lots=1,
            entry_price=book.inside_bid,
            entry_obi=0.9,
            opened_at=quote.timestamp,
        )
        self.assertTrue(platform.alpha_blocks_volume())

        step = platform.process_tick(quote, book, ledger, ctx)
        self.assertEqual(step.volume.action, "obi_priority_block")
        self.assertEqual(len(step.volume_fills), 0)

    def test_churn_runs_when_flat_and_obi_idle(self) -> None:
        platform = AlphaVolumePlatform.with_sniper_threshold(0.85)
        platform.volume.pulse_period_ms = CHURN_GENERATOR_PERIOD_MS
        book = l1_book(BALANCED_L1)
        quote = l1_quote(BALANCED_L1)
        ledger = PositionLedger()
        ctx = _flat_ctx(book)

        step = platform.process_tick(quote, book, ledger, ctx)
        self.assertTrue(step.volume.pulse_fired)
        self.assertIn(
            step.volume.action,
            ("quotes_armed", "quotes_working", "churn_cycle_complete", "churn_partial_fill"),
        )


if __name__ == "__main__":
    unittest.main()
