"""
Session mean-reversion on ZN — works when vol is moderate and no macro shock.
Best in US morning chop and post-auction digestion, not into FOMC.
"""

from __future__ import annotations

from zn_competition.strategies.base import Side, Signal, StrategyContext

# Block list: do not fade during these
BLOCKED_EVENTS = frozenset(
    {"FOMC_rate_decision", "NFP", "CPI", "10Y_auction", "30Y_auction"}
)


class SessionMeanReversionStrategy:
    name = "session_mean_reversion"

    def __init__(
        self,
        z_window_ticks: float = 2.0,
        entry_z: float = 1.25,
        exit_z: float = 0.35,
        size: int = 2,
        max_hold_seconds: int = 900,
    ):
        self.z_window_ticks = z_window_ticks
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.size = size
        self.max_hold_seconds = max_hold_seconds

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        if ctx.event_tag in BLOCKED_EVENTS:
            return None

        # Requires upstream feature: deviation from session VWAP in ticks
        z = ctx.extra.get("vwap_z")
        if z is None:
            return None

        if abs(z) < self.entry_z:
            if ctx.position != 0 and abs(z) < self.exit_z:
                return Signal(
                    side=Side.FLAT,
                    size=abs(ctx.position),
                    urgency="aggressive",
                    reason="mr_exit_vwap",
                    expected_edge_ticks=0.5,
                    max_hold_seconds=self.max_hold_seconds,
                )
            return None

        side = Side.SELL if z > 0 else Side.BUY
        return Signal(
            side=side,
            size=min(self.size, 10 - abs(ctx.position)),
            urgency="passive",
            reason="mr_enter_vwap",
            expected_edge_ticks=0.75,
            max_hold_seconds=self.max_hold_seconds,
        )
