"""
Event-risk sleeve: trade direction after surprise, not random pre-event lottery.
ZN moves fastest on CPI/NFP/FOMC and 10Y auction tails.
"""

from __future__ import annotations

from zn_competition.strategies.base import Side, Signal, StrategyContext

EVENT_SIZE = {
    "FOMC_rate_decision": 4,
    "NFP": 3,
    "CPI": 3,
    "10Y_auction": 2,
}


class MacroEventStrategy:
    name = "macro_event"

    def __init__(self, post_release_only: bool = True):
        self.post_release_only = post_release_only

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        tag = ctx.event_tag
        if not tag or tag not in EVENT_SIZE:
            return None

        phase = ctx.extra.get("event_phase")  # pre | release | post
        if self.post_release_only and phase != "post":
            return None

        surprise_bp = ctx.extra.get("surprise_10y_equiv_bp")
        if surprise_bp is None:
            return None

        # Positive surprise (hot print) -> yields up -> ZN down
        if surprise_bp > 0:
            side = Side.SELL
        elif surprise_bp < 0:
            side = Side.BUY
        else:
            return None

        size = min(EVENT_SIZE[tag], 10 - abs(ctx.position))
        if size <= 0:
            return None

        return Signal(
            side=side,
            size=size,
            urgency="aggressive",
            reason=f"macro_{tag}",
            expected_edge_ticks=2.0,
            max_hold_seconds=1800,
        )
