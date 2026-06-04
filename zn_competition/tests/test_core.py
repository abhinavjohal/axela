"""Stdlib unittest suite for ZN tick, fee, ledger, and backtest."""

from __future__ import annotations

import unittest

from zn_competition.backtest import generate_synthetic_quotes, run_backtest
from zn_competition.economics import (
    FeeAccounting,
    four_week_fee_budget,
    net_pnl_from_tick_move,
    validate_trade_lots,
)
from zn_competition.specs import MAX_POSITION_LOTS
from zn_competition.execution import execute_signal
from zn_competition.microstructure import Quote
from zn_competition.risk import (
    ExecutionRiskException,
    FillRecord,
    OrderRequest,
    PositionLedger,
    clip_order_size,
    validate_order,
)
from zn_competition.specs import (
    DOLLARS_PER_TICK,
    FEE_PER_LOT_PER_SIDE_USD,
    FEE_PER_LOT_ROUND_TURN_USD,
    TICK_SIZE_FLOAT,
    ZN_SEP26,
)
from zn_competition.strategies.base import Side, Signal


class TestZNConstants(unittest.TestCase):
    def test_tick_size(self) -> None:
        self.assertAlmostEqual(TICK_SIZE_FLOAT, 1 / 64)
        self.assertAlmostEqual(DOLLARS_PER_TICK, 15.625)

    def test_fees(self) -> None:
        self.assertEqual(FEE_PER_LOT_PER_SIDE_USD, 0.50)
        self.assertEqual(FEE_PER_LOT_ROUND_TURN_USD, 1.00)
        self.assertEqual(ZN_SEP26.fee_for_round_turn(3), 3.0)

    def test_round_price(self) -> None:
        p = ZN_SEP26.round_price_to_tick(112.015625)
        self.assertAlmostEqual(p, 112.015625)


class TestNetPnLFromTickMove(unittest.TestCase):
    def test_one_tick_one_lot_round_turn(self) -> None:
        result = net_pnl_from_tick_move(1.0, 1, sides=2)
        self.assertAlmostEqual(result.gross_pnl_usd, DOLLARS_PER_TICK)
        self.assertAlmostEqual(result.fees_usd, FEE_PER_LOT_ROUND_TURN_USD)
        self.assertAlmostEqual(
            result.net_pnl_usd,
            DOLLARS_PER_TICK - FEE_PER_LOT_ROUND_TURN_USD,
        )

    def test_three_ticks_five_lots(self) -> None:
        result = net_pnl_from_tick_move(3.0, 5)
        self.assertAlmostEqual(result.gross_pnl_usd, 3.0 * DOLLARS_PER_TICK * 5)
        self.assertAlmostEqual(result.fees_usd, 5.0 * FEE_PER_LOT_ROUND_TURN_USD)
        self.assertAlmostEqual(result.net_pnl_usd, result.gross_pnl_usd - result.fees_usd)

    def test_single_side_fee(self) -> None:
        result = net_pnl_from_tick_move(2.0, 2, sides=1)
        self.assertAlmostEqual(result.fees_usd, 2 * FEE_PER_LOT_PER_SIDE_USD)

    def test_adverse_tick_move(self) -> None:
        result = net_pnl_from_tick_move(-1.0, 1)
        self.assertAlmostEqual(result.gross_pnl_usd, -DOLLARS_PER_TICK)
        self.assertAlmostEqual(
            result.net_pnl_usd,
            -DOLLARS_PER_TICK - FEE_PER_LOT_ROUND_TURN_USD,
        )

    def test_lots_exceed_cap(self) -> None:
        with self.assertRaises(ValueError):
            net_pnl_from_tick_move(1.0, MAX_POSITION_LOTS + 1)

    def test_projected_position_exceeds_cap(self) -> None:
        with self.assertRaises(ValueError):
            net_pnl_from_tick_move(
                1.0,
                2,
                position_before=9,
                signed_lot_delta=2,
            )

    def test_validate_trade_lots_zero_rejected(self) -> None:
        with self.assertRaises(ValueError):
            validate_trade_lots(0)


class TestFeeAccounting(unittest.TestCase):
    def test_breakeven_per_leg(self) -> None:
        fees = FeeAccounting(200)
        self.assertAlmostEqual(fees.total_fees_usd, 100.0)
        self.assertAlmostEqual(fees.breakeven_ticks_per_leg_lot(), 0.032, places=3)

    def test_breakeven_round_turn(self) -> None:
        fees = FeeAccounting(0)
        self.assertAlmostEqual(fees.breakeven_ticks_per_round_turn(1), 0.064, places=3)

    def test_four_week_budget(self) -> None:
        budget = four_week_fee_budget()
        self.assertEqual(budget["total_legs"], 2000)
        self.assertEqual(budget["total_fees_usd"], 1000.0)


class TestPositionLedger(unittest.TestCase):
    def test_round_turn_long_one_tick_profit(self) -> None:
        ledger = PositionLedger()
        entry = 112.0
        exit_p = entry + TICK_SIZE_FLOAT
        ledger.apply_fill(FillRecord(Side.BUY, 1, entry, 0.5, "t1", "open"))
        ledger.apply_fill(FillRecord(Side.SELL, 1, exit_p, 0.5, "t2", "close"))
        self.assertEqual(ledger.position, 0)
        self.assertEqual(ledger.leg_lots_traded, 2)
        self.assertAlmostEqual(ledger.total_fees_usd, FEE_PER_LOT_ROUND_TURN_USD)
        self.assertAlmostEqual(
            ledger.realized_pnl_usd,
            DOLLARS_PER_TICK - FEE_PER_LOT_ROUND_TURN_USD,
            places=4,
        )

    def test_position_cap_clip(self) -> None:
        self.assertEqual(clip_order_size(5, 8), 2)
        self.assertEqual(clip_order_size(5, 10), 0)


class TestExecution(unittest.TestCase):
    def test_max_position_validation(self) -> None:
        validate_order(8, OrderRequest(Side.BUY, 2, "ok"))
        with self.assertRaises(ExecutionRiskException):
            validate_order(9, OrderRequest(Side.BUY, 2, "breach"))

    def test_execute_signal(self) -> None:
        quote = Quote("t", 112.0, 112.03125, 10, 10)
        signal = Signal(Side.BUY, 1, "passive", "test", 1.0)
        fill = execute_signal(signal, quote, 0)
        self.assertIsNotNone(fill)
        assert fill is not None
        self.assertEqual(fill.lots, 1)
        self.assertEqual(fill.fee_usd, FEE_PER_LOT_PER_SIDE_USD)


class TestBacktest(unittest.TestCase):
    def test_synthetic_run(self) -> None:
        quotes = generate_synthetic_quotes(300)
        result = run_backtest(
            quotes,
            week=1,
            use_alpha_volume_platform=False,
            obi_entry_threshold=0.7,
        )
        self.assertGreaterEqual(result.leg_lots_traded, 0)
        self.assertIsInstance(result.net_pnl_usd, float)


if __name__ == "__main__":
    unittest.main()
