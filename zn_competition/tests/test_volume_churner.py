"""Tests for VolumeChurner Module 4 generator pulse execution."""

from __future__ import annotations

import unittest

from zn_competition.microstructure import OrderBookSnapshot, Quote
from zn_competition.risk import PositionLedger
from zn_competition.specs import FEE_PER_LOT_ROUND_TURN_USD, WEEKLY_VOLUME_MIN
from zn_competition.strategies.base import StrategyContext
from zn_competition.strategies.session_mr import (
    CHURN_GENERATOR_PERIOD_MS,
    VolumeChurner,
    VolumeChurnerExecutionEngine,
)


def _book() -> OrderBookSnapshot:
    return OrderBookSnapshot(
        timestamp="2026-06-03T14:00:00+00:00",
        bid=112.0,
        ask=112.03125,
        direct_bid_qty=20,
        direct_ask_qty=20,
        bid_order_count=4,
        ask_order_count=4,
    )


def _ctx(week: int = 1, legs: int = 0, position: int = 0) -> StrategyContext:
    required = WEEKLY_VOLUME_MIN[week - 1]
    return StrategyContext(
        mid_price=112.015625,
        bid=112.0,
        ask=112.03125,
        position=position,
        week_number=week,
        leg_lots_traded_this_week=legs,
        leg_lots_traded_total=legs,
        weekly_min_remaining=max(0, required - legs),
        book=_book(),
    )


def _quote(ts: str = "2026-06-03T14:00:00+00:00") -> Quote:
    book = _book()
    return Quote(
        ts,
        book.bid,
        book.ask,
        direct_bid_qty=book.direct_bid_qty,
        direct_ask_qty=book.direct_ask_qty,
    )


class TestVolumeChurnerQuotes(unittest.TestCase):
    def test_place_offsetting_inside_quotes(self) -> None:
        book = _book()
        pair = VolumeChurner.place_offsetting_inside_quotes(book, book.timestamp, 1)
        self.assertEqual(pair.bid.limit_price, book.inside_bid)
        self.assertEqual(pair.ask.limit_price, book.inside_ask)
        self.assertEqual(pair.bid.lots, 1)
        self.assertEqual(pair.ask.lots, 1)

    def test_only_runs_when_flat(self) -> None:
        churner = VolumeChurner()
        self.assertFalse(churner.should_run(_ctx(position=2)))


class TestGeneratorPulse(unittest.TestCase):
    def test_default_period_is_30_seconds(self) -> None:
        engine = VolumeChurnerExecutionEngine()
        self.assertEqual(engine.pulse_period_ms, CHURN_GENERATOR_PERIOD_MS)
        self.assertEqual(CHURN_GENERATOR_PERIOD_MS, 30_000)

    def test_pulse_wait_between_intervals(self) -> None:
        churner = VolumeChurner()
        ledger = PositionLedger()
        book = _book()
        first = churner.process_generator_pulse(
            _quote("2026-06-03T14:00:00+00:00"), book, ledger, _ctx()
        )
        self.assertTrue(first.pulse_fired)

        second = churner.process_generator_pulse(
            _quote("2026-06-03T14:00:15+00:00"), book, ledger, _ctx()
        )
        self.assertEqual(second.action, "pulse_wait")
        self.assertFalse(second.pulse_fired)

    def test_token_dropped_when_not_flat(self) -> None:
        churner = VolumeChurner()
        ledger = PositionLedger(position=1)
        book = _book()
        result = churner.process_generator_pulse(
            _quote(), book, ledger, _ctx(position=1)
        )
        self.assertEqual(result.action, "token_dropped")
        self.assertFalse(result.execution_token_active)
        self.assertEqual(len(result.fills), 0)

    def test_flat_pulse_arms_two_inside_quotes(self) -> None:
        churner = VolumeChurner()
        ledger = PositionLedger()
        book = _book()
        result = churner.process_generator_pulse(
            _quote(), book, ledger, _ctx()
        )
        self.assertIn(result.action, ("quotes_armed", "churn_cycle_complete", "quotes_working"))
        self.assertTrue(result.pulse_fired)


class TestVolumeChurnerExecution(unittest.TestCase):
    def test_churn_cycle_adds_two_legs(self) -> None:
        churner = VolumeChurner()
        ledger = PositionLedger()
        book = _book()
        quote = _quote()
        ctx = _ctx()
        result = churner.process_generator_pulse(quote, book, ledger, ctx)
        self.assertEqual(ledger.position, 0)
        self.assertEqual(ledger.leg_lots_traded, 2)
        self.assertEqual(len(result.fills), 2)
        self.assertEqual(result.action, "churn_cycle_complete")

    def test_shuts_off_when_weekly_quota_met(self) -> None:
        churner = VolumeChurner()
        ledger = PositionLedger()
        ledger.leg_lots_traded = WEEKLY_VOLUME_MIN[0]
        book = _book()
        quote = _quote()
        ctx = _ctx(legs=WEEKLY_VOLUME_MIN[0])
        result = churner.process_generator_pulse(quote, book, ledger, ctx)
        self.assertTrue(result.quota_satisfied)
        self.assertEqual(result.action, "quota_off")
        self.assertEqual(len(result.fills), 0)

    def test_scratch_cost_per_cycle(self) -> None:
        churner = VolumeChurner()
        self.assertAlmostEqual(
            churner.expected_scratch_cost_usd(1),
            -FEE_PER_LOT_ROUND_TURN_USD,
        )


if __name__ == "__main__":
    unittest.main()
