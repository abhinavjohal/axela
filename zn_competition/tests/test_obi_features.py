"""OBI calculator and feature pipeline tests."""

from __future__ import annotations

import unittest

from zn_competition.features import MicrostructureFeatureEngine, OBIHistoryBuffer
from zn_competition.microstructure import (
    OrderBookImbalanceCalculator,
    OrderBookSnapshot,
    Quote,
    calculate_order_book_imbalance,
)
from zn_competition.specs import TICK_SIZE_FLOAT, ZN_SEP26


class TestOrderBookImbalanceCalculator(unittest.TestCase):
    def test_l1_l2_formula(self) -> None:
        result = OrderBookImbalanceCalculator.from_quantities(60, 5, 50, 5)
        self.assertEqual(result.bid_qty, 110)
        self.assertEqual(result.ask_qty, 10)
        self.assertAlmostEqual(result.obi, (110 - 10) / 120)

    def test_zero_total_qty(self) -> None:
        result = OrderBookImbalanceCalculator.from_quantities(0, 0, 0, 0)
        self.assertEqual(result.obi, 0.0)

    def test_snapshot_equivalence(self) -> None:
        book = OrderBookSnapshot("t", 112.0, 112.03125, 60, 5, 50, 5)
        self.assertAlmostEqual(
            calculate_order_book_imbalance(book),
            OrderBookImbalanceCalculator.from_snapshot(book).obi,
        )

    def test_l1_only_matches_legacy(self) -> None:
        result = OrderBookImbalanceCalculator.from_quantities(80, 20, 0, 0)
        self.assertAlmostEqual(result.obi, (80 - 20) / 100)


class TestFeaturePipelineOBI(unittest.TestCase):
    def test_obi_history_array(self) -> None:
        engine = MicrostructureFeatureEngine(obi_history_length=10)
        quotes = [
            Quote(f"t{i}", 112.0, 112.03125, 70, 10, 50, 10)
            for i in range(5)
        ]
        snapshots = engine.process_quotes(quotes)
        self.assertEqual(len(snapshots), 5)
        self.assertEqual(len(engine.obi_history), 5)
        self.assertAlmostEqual(snapshots[-1].obi, engine.obi_history[-1])
        self.assertAlmostEqual(snapshots[-1].book_imbalance, snapshots[-1].obi)

    def test_spread_uses_zn_tick_size(self) -> None:
        half = TICK_SIZE_FLOAT / 2
        quote = Quote("t", 112.0, 112.0 + TICK_SIZE_FLOAT, 10, 10)
        snap = MicrostructureFeatureEngine().update(quote)
        self.assertAlmostEqual(snap.spread_ticks, 1.0, places=4)

    def test_vol_uses_zn_tick_size(self) -> None:
        engine = MicrostructureFeatureEngine(vol_window=5)
        q1 = Quote("t1", 112.0, 112.03125, 10, 10)
        q2 = Quote("t2", 112.0 + TICK_SIZE_FLOAT, 112.03125 + TICK_SIZE_FLOAT, 10, 10)
        engine.update(q1)
        snap = engine.update(q2)
        self.assertGreater(snap.realized_vol_ticks_1h, 0.0)


class TestOBIHistoryBuffer(unittest.TestCase):
    def test_rolling_window(self) -> None:
        buf = OBIHistoryBuffer(max_length=3)
        for v in (0.1, 0.2, 0.3, 0.4):
            buf.append(v)
        self.assertEqual(buf.values, (0.2, 0.3, 0.4))


if __name__ == "__main__":
    unittest.main()
