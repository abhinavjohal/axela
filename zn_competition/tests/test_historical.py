"""Historical loop and execution cap tests."""

from __future__ import annotations

import unittest

from zn_competition.execution import execute_signal
from zn_competition.historical import run_historical_loop
from zn_competition.microstructure import Quote
from zn_competition.risk import (
    ExecutionRiskException,
    enforce_order_size,
    validate_order,
)
from zn_competition.strategies.base import Side
from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD, MAX_POSITION_LOTS
from zn_competition.strategies.base import Side, Signal


class TestExecutionCap(unittest.TestCase):
    def test_enforce_raises_on_breach(self) -> None:
        with self.assertRaises(ExecutionRiskException) as ctx:
            enforce_order_size(5, position=8, side=Side.BUY)
        self.assertIn("POSITION_CAP", str(ctx.exception))

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
        summary = run_historical_loop(week=1)
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


if __name__ == "__main__":
    unittest.main()
