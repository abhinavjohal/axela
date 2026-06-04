"""OBI calculator and feature pipeline tests."""

from __future__ import annotations

import unittest

from zn_competition.features import (
    FEATURE_TABLE_COLUMNS,
    MicrostructureFeatureEngine,
    OBIHistoryBuffer,
)
from zn_competition.microstructure import (
    OrderBookImbalanceCalculator,
    OrderBookSnapshot,
    Quote,
    calculate_direct_obi,
    calculate_order_book_imbalance,
    parse_level1_book_fields,
)
from zn_competition.specs import TICK_SIZE_FLOAT, ZN_SEP26


class TestDirectOBIParser(unittest.TestCase):
    def test_direct_obi_formula(self) -> None:
        result = parse_level1_book_fields(110, 10, 20, 5)
        self.assertAlmostEqual(result.direct_obi, (110 - 10) / 120)
        self.assertAlmostEqual(result.avg_bid_order_size, 110 / 20)
        self.assertAlmostEqual(result.avg_ask_order_size, 10 / 5)

    def test_calculate_direct_obi_function(self) -> None:
        self.assertAlmostEqual(calculate_direct_obi(80, 20), 0.6)

    def test_zero_total_qty(self) -> None:
        result = parse_level1_book_fields(0, 0, 0, 0)
        self.assertEqual(result.direct_obi, 0.0)
        self.assertEqual(result.avg_bid_order_size, 0.0)
        self.assertEqual(result.avg_ask_order_size, 0.0)

    def test_snapshot_equivalence(self) -> None:
        book = OrderBookSnapshot("t", 112.0, 112.03125, 110, 10, 20, 5)
        self.assertAlmostEqual(
            calculate_order_book_imbalance(book),
            OrderBookImbalanceCalculator.from_snapshot(book).direct_obi,
        )

    def test_calculator_from_quantities(self) -> None:
        result = OrderBookImbalanceCalculator.from_quantities(80, 20, 4, 2)
        self.assertAlmostEqual(result.direct_obi, (80 - 20) / 100)
        self.assertAlmostEqual(result.avg_bid_order_size, 20.0)
        self.assertAlmostEqual(result.avg_ask_order_size, 10.0)


class TestFeaturePipelineOBI(unittest.TestCase):
    def test_obi_history_array(self) -> None:
        engine = MicrostructureFeatureEngine(obi_history_length=10)
        quotes = [
            Quote(
                f"t{i}",
                112.0,
                112.03125,
                direct_bid_qty=70,
                direct_ask_qty=10,
                bid_order_count=7,
                ask_order_count=2,
            )
            for i in range(5)
        ]
        snapshots = engine.process_quotes(quotes)
        self.assertEqual(len(snapshots), 5)
        self.assertEqual(len(engine.obi_history), 5)
        self.assertAlmostEqual(snapshots[-1].direct_obi, engine.obi_history[-1])
        self.assertAlmostEqual(snapshots[-1].book_imbalance, snapshots[-1].direct_obi)

    def test_feature_table_columns(self) -> None:
        engine = MicrostructureFeatureEngine()
        quote = Quote(
            "t",
            112.0,
            112.03125,
            direct_bid_qty=100,
            direct_ask_qty=50,
            bid_order_count=10,
            ask_order_count=5,
        )
        engine.update(quote)
        table = engine.feature_table()
        self.assertEqual(len(table), 1)
        row = table[0]
        self.assertEqual(tuple(row.keys()), FEATURE_TABLE_COLUMNS)
        self.assertAlmostEqual(row["direct_obi"], (100 - 50) / 150)
        self.assertAlmostEqual(row["avg_bid_order_size"], 10.0)
        self.assertAlmostEqual(row["avg_ask_order_size"], 10.0)

    def test_spread_uses_zn_tick_size(self) -> None:
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
        self.assertAlmostEqual(ZN_SEP26.price_delta_to_ticks(TICK_SIZE_FLOAT), 1.0)


class TestOBIHistoryBuffer(unittest.TestCase):
    def test_rolling_window(self) -> None:
        buf = OBIHistoryBuffer(max_length=3)
        for v in (0.1, 0.2, 0.3, 0.4):
            buf.append(v)
        self.assertEqual(buf.values, (0.2, 0.3, 0.4))


if __name__ == "__main__":
    unittest.main()
