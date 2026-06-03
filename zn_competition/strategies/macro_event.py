"""
Macro event sleeve — post-release directional trades with fee-aware edge filter.
Hot surprise (yields up) -> sell ZN; cold surprise -> buy ZN.
"""

from __future__ import annotations

from zn_competition.specs import ZN_SEP26
from zn_competition.strategies.base import Side, Signal, StrategyContext

EVENT_SIZE: dict[str, int] = {
    "FOMC_rate_decision": 4,
    "NFP": 3,
    "CPI": 3,
    "10Y_auction": 2,
}

MIN_SURPRISE_BP = 0.5
HIGH_VOL_TICKS_1H = 8.0
MIN_NET_EDGE_TICKS = ZN_SEP26.dollars_to_ticks(ZN_SEP26.fee_round_turn, lots=1) + 1.0


class MacroEventStrategy:
    name = "macro_event"

    def __init__(self, post_release_only: bool = True) -> None:
        self.post_release_only = post_release_only

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        tag = ctx.event_tag
        if not tag or tag not in EVENT_SIZE:
            return None

        if self.post_release_only and ctx.event_phase != "post":
            return None

        surprise = ctx.surprise_10y_equiv_bp
        if surprise is None or abs(surprise) < MIN_SURPRISE_BP:
            return None

        side = Side.SELL if surprise > 0 else Side.BUY
        base_lots = EVENT_SIZE[tag]
        if ctx.features and ctx.features.realized_vol_ticks_1h > HIGH_VOL_TICKS_1H:
            base_lots = max(1, base_lots // 2)

        lots = min(base_lots, ZN_SEP26.max_position_lots - abs(ctx.position))
        if lots <= 0:
            return None

        expected_ticks = min(abs(surprise) * 1.5, 6.0)
        signal = Signal(
            side=side,
            size=lots,
            urgency="aggressive",
            reason=f"macro_{tag}",
            expected_edge_ticks=expected_ticks,
            max_hold_seconds=1800,
        )
        if signal.net_edge_after_round_turn_fee_ticks(lots) < MIN_NET_EDGE_TICKS:
            return None
        return signal
