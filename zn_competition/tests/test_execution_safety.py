"""Execution safety gate and regime purge tests."""

from __future__ import annotations

import unittest

from zn_competition.execution import (
    DiscreteOrderInputs,
    ExecutionEngine,
    StrategyRegime,
    execute_signal,
)
from zn_competition.microstructure import Quote
from zn_competition.risk import (
    ExecutionRiskException,
    OrderRequest,
    PositionGuard,
    PositionLedger,
    gate_order_submission,
    signed_incoming_lots,
)
from zn_competition.specs import MAX_POSITION_LOTS, ZN_SEP26
from zn_competition.strategies.base import Side, Signal
from zn_competition.strategies.engine import StrategyStack, regime_for_strategy


class TestPositionGuard(unittest.TestCase):
    def test_tracks_abs_net_position(self) -> None:
        guard = PositionGuard()
        ledger = PositionLedger(position=-3)
        guard.sync_from_ledger(ledger)
        self.assertEqual(guard.net_position, -3)
        self.assertEqual(guard.abs_net_position, 3)

    def test_less_than_guard_passes_buy_within_cap(self) -> None:
        guard = PositionGuard()
        self.assertTrue(guard.less_than_guard_passes(2, Side.BUY, current_position=8))

    def test_less_than_guard_fails_buy_over_cap(self) -> None:
        guard = PositionGuard()
        self.assertFalse(guard.less_than_guard_passes(5, Side.BUY, current_position=8))

    def test_validate_raises_position_guard_exception(self) -> None:
        guard = PositionGuard()
        with self.assertRaises(ExecutionRiskException) as ctx:
            guard.validate_submission(8, 5, Side.BUY)
        self.assertIn("POSITION_GUARD", str(ctx.exception))
        self.assertIn("LessThan_Guard", str(ctx.exception))

    def test_signed_delta_buy(self) -> None:
        self.assertEqual(signed_incoming_lots(Side.BUY, 3, 0), 3)

    def test_signed_delta_sell(self) -> None:
        self.assertEqual(signed_incoming_lots(Side.SELL, 3, 5), -3)

    def test_gate_blocks_buy_at_cap(self) -> None:
        with self.assertRaises(ExecutionRiskException) as ctx:
            gate_order_submission(8, 5, Side.BUY)
        self.assertIn("POSITION_GUARD", str(ctx.exception))
        self.assertIn("13", str(ctx.exception))

    def test_gate_allows_sell_reducing_long(self) -> None:
        gate_order_submission(8, 5, Side.SELL)

    def test_abs_position_plus_signed_formula(self) -> None:
        position = 8
        lots = 5
        signed = signed_incoming_lots(Side.BUY, lots, position)
        self.assertGreater(abs(position + signed), MAX_POSITION_LOTS)


class TestDiscreteOrderPlacement(unittest.TestCase):
    def test_trigger_false_skips_routing(self) -> None:
        ledger = PositionLedger()
        engine = ExecutionEngine()
        quote = Quote("t", 112.0, 112.03125, 10, 10)
        inputs = DiscreteOrderInputs(
            instrument=ZN_SEP26.tt_instrument,
            price=112.0,
            quantity=1,
            trigger=False,
            side=Side.BUY,
            reason="test",
        )
        result = engine.place_discrete_order(ledger, inputs, quote)
        self.assertIsNone(result)
        self.assertEqual(ledger.position, 0)

    def test_guard_blocks_before_fill(self) -> None:
        ledger = PositionLedger(position=9)
        engine = ExecutionEngine()
        quote = Quote("t", 112.0, 112.03125, 10, 10)
        inputs = DiscreteOrderInputs(
            instrument=ZN_SEP26.tt_instrument,
            price=112.0,
            quantity=2,
            trigger=True,
            side=Side.BUY,
            reason="test",
        )
        with self.assertRaises(ExecutionRiskException):
            engine.place_discrete_order(ledger, inputs, quote)
        self.assertEqual(ledger.position, 9)
        self.assertEqual(len(ledger.fills), 0)


class TestExecutionEngine(unittest.TestCase):
    def test_submit_reads_ledger_position(self) -> None:
        ledger = PositionLedger(position=9)
        engine = ExecutionEngine()
        quote = Quote("t", 112.0, 112.03125, 10, 10)
        with self.assertRaises(ExecutionRiskException):
            engine.submit_order(
                ledger,
                OrderRequest(Side.BUY, 2, "test"),
                quote,
                "passive",
            )

    def test_purge_trend_on_regime_flip(self) -> None:
        engine = ExecutionEngine()
        engine.register_working_order(
            OrderRequest(Side.BUY, 1, "macro_test"),
            StrategyRegime.TREND_FOLLOWING,
            "macro_event",
            "passive",
            "t1",
        )
        engine.register_working_order(
            OrderRequest(Side.BUY, 1, "mr_test"),
            StrategyRegime.MEAN_REVERSION,
            "session_mean_reversion",
            "passive",
            "t2",
        )
        report = engine.purge_regime(StrategyRegime.TREND_FOLLOWING)
        self.assertEqual(report.canceled_count, 1)
        active = engine.active_working_orders()
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].regime, StrategyRegime.MEAN_REVERSION)


class TestStrategyEngineRegimeFlip(unittest.TestCase):
    def test_regime_mapping(self) -> None:
        self.assertEqual(regime_for_strategy("macro_event"), StrategyRegime.TREND_FOLLOWING)
        self.assertEqual(
            regime_for_strategy("session_mean_reversion"),
            StrategyRegime.MEAN_REVERSION,
        )

    def test_trend_to_mr_purges_trend_orders(self) -> None:
        stack = StrategyStack()
        stack.execution.register_working_order(
            OrderRequest(Side.BUY, 1, "macro_pending"),
            StrategyRegime.TREND_FOLLOWING,
            "macro_event",
            "passive",
            "t0",
        )
        stack._active_regime = StrategyRegime.TREND_FOLLOWING
        stack._active_state = stack._state_from_regime(StrategyRegime.TREND_FOLLOWING)

        purge = stack._handle_regime_transition(
            StrategyRegime.MEAN_REVERSION,
            "session_mean_reversion",
        )
        self.assertIsNotNone(purge)
        assert purge is not None
        self.assertEqual(purge.canceled_count, 1)
        self.assertEqual(stack.active_regime, StrategyRegime.MEAN_REVERSION)


class TestLegacyExecuteSignal(unittest.TestCase):
    def test_raises_execution_risk_exception(self) -> None:
        quote = Quote("t", 112.0, 112.03125, 10, 10)
        signal = Signal(Side.BUY, 5, "passive", "test", 1.0)
        with self.assertRaises(ExecutionRiskException):
            execute_signal(signal, quote, position=8)


if __name__ == "__main__":
    unittest.main()
