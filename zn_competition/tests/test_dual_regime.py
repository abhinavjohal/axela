"""Dual-regime session clock and OBI engine tests."""

from __future__ import annotations

import unittest

from zn_competition.strategies.obi_regime import (
    DualRegimeSessionClock,
    OBIRegimeMode,
    SNIPER_THRESHOLD,
    VOLUME_THRESHOLD,
)
from zn_competition.strategies.volume_aware_mm import DualRegimeOBIEngine


class TestDualRegimeSessionClock(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = DualRegimeSessionClock()

    def test_sniper_window_morning_et(self) -> None:
        # 09:00 ET = 13:00 UTC (EDT)
        snap = self.clock.evaluate("2026-05-15T13:00:00+00:00")
        self.assertEqual(snap.mode, OBIRegimeMode.SNIPER_MODE)
        self.assertAlmostEqual(snap.entry_threshold, SNIPER_THRESHOLD)

    def test_volume_window_midday_et(self) -> None:
        # 13:00 ET = 17:00 UTC (EDT)
        snap = self.clock.evaluate("2026-05-15T17:00:00+00:00")
        self.assertEqual(snap.mode, OBIRegimeMode.VOLUME_MODE)
        self.assertAlmostEqual(snap.entry_threshold, VOLUME_THRESHOLD)

    def test_off_between_windows(self) -> None:
        # 11:45 ET = 15:45 UTC (EDT) — between sniper end and volume start
        snap = self.clock.evaluate("2026-05-15T15:45:00+00:00")
        self.assertEqual(snap.mode, OBIRegimeMode.OFF)
        self.assertFalse(snap.allows_new_entries)

    def test_regime_shift_detected(self) -> None:
        self.clock.evaluate("2026-05-15T13:00:00+00:00")
        snap2 = self.clock.evaluate("2026-05-15T17:00:00+00:00")
        self.assertTrue(self.clock.regime_changed(snap2))


class TestDualRegimeOBIEngine(unittest.TestCase):
    def test_cancel_stale_on_regime_apply(self) -> None:
        engine = DualRegimeOBIEngine()
        from zn_competition.strategies.base import Side
        from zn_competition.strategies.obi_regime import OBIRegimeSnapshot
        from zn_competition.strategies.volume_aware_mm import WorkingLimitOrder

        engine._working_order = WorkingLimitOrder(
            side=Side.BUY,
            lots=1,
            limit_price=112.0,
            placed_at="t",
            reason="test",
        )
        snap = OBIRegimeSnapshot(
            mode=OBIRegimeMode.VOLUME_MODE,
            entry_threshold=0.65,
            flip_threshold=-0.65,
            short_entry_threshold=-0.65,
        )
        engine.apply_regime(snap)
        self.assertTrue(engine.cancel_stale_resting_orders())
        self.assertIsNone(engine._working_order)


if __name__ == "__main__":
    unittest.main()
