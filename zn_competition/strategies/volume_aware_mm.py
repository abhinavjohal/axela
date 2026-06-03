"""
Volume-completion sleeve — passive quotes when weekly minimum at risk.
Explicitly accepts sub-breakeven tick edge; capped adverse selection.
"""

from __future__ import annotations

from zn_competition.specs import HIGH_IMPACT_MACRO_TAGS, ZN_SEP26
from zn_competition.strategies.base import Side, Signal, StrategyContext

MAX_VOL_TICKS_1H = 4.0
IMBALANCE_THRESHOLD = 0.15
MAX_SPREAD_TICKS = 2.0
MAX_NEGATIVE_EDGE_TICKS = -0.15


class VolumeAwareMarketMaking:
    name = "volume_aware_mm"

    def __init__(
        self,
        quote_size: int = 1,
        weekly_min_urgency_threshold: int = 40,
    ) -> None:
        if quote_size < 1:
            raise ValueError("quote_size must be >= 1")
        self.quote_size = quote_size
        self.weekly_min_urgency_threshold = weekly_min_urgency_threshold

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        if ctx.weekly_min_remaining <= 0:
            return None
        if ctx.event_tag in HIGH_IMPACT_MACRO_TAGS:
            return None
        if ctx.features is None:
            return None
        if ctx.features.realized_vol_ticks_1h > MAX_VOL_TICKS_1H:
            return None
        if ctx.features.spread_ticks > MAX_SPREAD_TICKS:
            return None
        if ctx.weekly_min_remaining > self.weekly_min_urgency_threshold:
            return None

        imb = ctx.features.book_imbalance
        if imb > IMBALANCE_THRESHOLD:
            side = Side.SELL
        elif imb < -IMBALANCE_THRESHOLD:
            side = Side.BUY
        else:
            return None

        lots = min(self.quote_size, ZN_SEP26.max_position_lots - abs(ctx.position))
        if lots <= 0:
            return None

        fee_ticks_rt = ZN_SEP26.dollars_to_ticks(ZN_SEP26.fee_round_turn * lots, lots=lots)
        expected_edge = max(MAX_NEGATIVE_EDGE_TICKS, -fee_ticks_rt * 0.5)

        return Signal(
            side=side,
            size=lots,
            urgency="passive",
            reason="volume_pad_passive",
            expected_edge_ticks=expected_edge,
            max_hold_seconds=120,
        )
