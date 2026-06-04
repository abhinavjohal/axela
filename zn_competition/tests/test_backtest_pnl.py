"""Backtest line-by-line P&L and fee schedule tests."""

from __future__ import annotations

import unittest

from zn_competition.backtest import generate_synthetic_quotes, run_backtest
from zn_competition.risk import PositionLedger
from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD, FEE_PER_LOT_ROUND_TURN_USD
from zn_competition.strategies.base import Side
from zn_competition.strategies.engine import StrategyStack


class TestBacktestPnL(unittest.TestCase):
    def test_fee_schedule_per_leg(self) -> None:
        result = run_backtest(
            generate_synthetic_quotes(50),
            week=1,
            use_volume_aware_mm=False,
        )
        self.assertEqual(
            result.total_fees_usd,
            result.leg_lots_traded * FEE_PER_LOT_PER_SIDE_USD,
        )

    def test_line_by_line_net_sums_to_realized(self) -> None:
        result = run_backtest(
            generate_synthetic_quotes(30),
            week=1,
            use_volume_aware_mm=False,
        )
        if result.pnl_lines:
            line_net = sum(line.net_pnl_usd for line in result.pnl_lines)
            self.assertAlmostEqual(line_net, result.realized_pnl_usd, places=2)

    def test_net_equals_gross_minus_fees(self) -> None:
        result = run_backtest(
            generate_synthetic_quotes(40),
            week=1,
            use_volume_aware_mm=False,
        )
        self.assertEqual(result.position_end, 0)
        self.assertAlmostEqual(
            result.net_pnl_usd,
            result.gross_pnl_usd - result.total_fees_usd,
            places=2,
        )

    def test_vamm_net_pnl_curve(self) -> None:
        result = run_backtest(generate_synthetic_quotes(60), week=1)
        self.assertEqual(result.position_end, 0)
        self.assertEqual(len(result.net_pnl_curve), 60)
        self.assertAlmostEqual(
            result.net_pnl_curve[-1].cumulative_net_pnl_usd,
            result.net_pnl_usd,
            places=2,
        )
        self.assertAlmostEqual(
            result.net_pnl_usd,
            result.gross_pnl_usd - result.total_fees_usd,
            places=2,
        )

    def test_round_turn_fee_on_flat_cycle(self) -> None:
        ledger = PositionLedger()
        from zn_competition.execution import execute_order
        from zn_competition.microstructure import Quote
        from zn_competition.risk import OrderRequest

        quote = Quote("t", 112.0, 112.03125, 10, 10)
        buy = execute_order(OrderRequest(Side.BUY, 1, "t"), quote, 0, "passive")
        ledger.apply_fill(buy)
        sell = execute_order(OrderRequest(Side.SELL, 1, "t"), quote, ledger.position, "passive")
        ledger.apply_fill(sell)
        self.assertAlmostEqual(ledger.total_fees_usd, FEE_PER_LOT_ROUND_TURN_USD)
        self.assertEqual(ledger.leg_lots_traded, 2)


if __name__ == "__main__":
    unittest.main()
