"""Historical loop and execution cap tests."""

from __future__ import annotations

import unittest
from pathlib import Path

from zn_competition.execution import execute_signal
from zn_competition.historical import (
    DEFAULT_ZN_MIN_DATA_PATH,
    generate_mock_order_book_stream,
    load_min_csv,
    load_zn_min_csv,
    row_to_level1_dict,
    run_historical_loop,
)
from zn_competition.microstructure import Quote
from zn_competition.risk import (
    ExecutionRiskException,
    enforce_order_size,
    validate_order,
)
from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD, MAX_POSITION_LOTS
from zn_competition.strategies.base import Side, Signal


class TestExecutionCap(unittest.TestCase):
    def test_enforce_raises_on_breach(self) -> None:
        with self.assertRaises(ExecutionRiskException) as ctx:
            enforce_order_size(5, position=8, side=Side.BUY)
        self.assertIn("POSITION_GUARD", str(ctx.exception))

    def test_validate_order_raises_execution_exception(self) -> None:
        from zn_competition.risk import OrderRequest

        with self.assertRaises(ExecutionRiskException):
            validate_order(9, OrderRequest(Side.BUY, 2, "test"))

    def test_execute_signal_raises_not_clips(self) -> None:
        quote = Quote("t", 112.0, 112.03125, 10, 10)
        signal = Signal(Side.BUY, 5, "passive", "test", 1.0)
        with self.assertRaises(ExecutionRiskException):
            execute_signal(signal, quote, position=8)


class TestHistoricalLoop(unittest.TestCase):
    def test_run_prints_summary_fields(self) -> None:
        summary = run_historical_loop(
            quotes=generate_mock_order_book_stream(count=80),
            week=1,
        )
        self.assertGreater(summary.ticks_processed, 0)
        self.assertGreaterEqual(summary.total_lots_traded, 0)
        self.assertAlmostEqual(
            summary.total_transaction_fees_usd,
            summary.total_lots_traded * FEE_PER_LOT_PER_SIDE_USD,
        )
        self.assertLessEqual(abs(summary.position_end), MAX_POSITION_LOTS)
        report = summary.format_report()
        self.assertIn("Total Lots Traded", report)
        self.assertIn("Gross P&L", report)
        self.assertIn("Net P&L", report)


class TestZnMinCsvParser(unittest.TestCase):
    def test_row_to_level1_from_ohlc(self) -> None:
        row = {
            "Timestamp (UTC)": "2026-05-12 23:23:00",
            "Open": "109.796875",
            "High": "109.796875",
            "Low": "109.781250",
            "Close": "109.781250",
        }
        fields = row_to_level1_dict(row)
        self.assertEqual(fields["timestamp"], "2026-05-12T23:23:00+00:00")
        self.assertIn("direct_bid_price", fields)
        self.assertIn("direct_ask_price", fields)
        self.assertGreater(fields["direct_bid_qty"], 0)
        self.assertGreater(fields["direct_ask_qty"], 0)
        self.assertGreater(fields["bid_order_count"], 0)
        self.assertGreater(fields["ask_order_count"], 0)
        self.assertLessEqual(fields["direct_bid_price"], fields["direct_ask_price"])

    def test_row_to_level1_explicit_columns(self) -> None:
        row = {
            "timestamp": "2026-06-01T12:00:00+00:00",
            "direct_bid_price": "112.0",
            "direct_ask_price": "112.03125",
            "direct_bid_qty": "50",
            "direct_ask_qty": "20",
            "bid_order_count": "5",
            "ask_order_count": "2",
        }
        fields = row_to_level1_dict(row)
        self.assertEqual(fields["direct_bid_qty"], 50)
        self.assertEqual(fields["direct_ask_qty"], 20)
        self.assertEqual(fields["bid_order_count"], 5)
        self.assertEqual(fields["ask_order_count"], 2)

    def test_load_zn_min_csv_chronological(self) -> None:
        if not DEFAULT_ZN_MIN_DATA_PATH.is_file():
            self.skipTest("zn_min_data.csv not present")
        quotes = load_min_csv(DEFAULT_ZN_MIN_DATA_PATH, instrument_id="ZN")
        self.assertGreater(len(quotes), 100)
        timestamps = [q.timestamp for q in quotes]
        self.assertEqual(timestamps, sorted(timestamps))
        self.assertGreater(quotes[0].direct_bid_qty, 0)
        self.assertGreater(quotes[0].direct_ask_qty, 0)


if __name__ == "__main__":
    unittest.main()
