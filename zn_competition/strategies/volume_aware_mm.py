"""
Volume-aware passive quoting — use only when weekly min not met and primary sleeves idle.
Purpose: complete 200+ lots with controlled adverse selection, not primary alpha.
"""

from __future__ import annotations

from zn_competition.strategies.base import Side, Signal, StrategyContext


class VolumeAwareMarketMaking:
    name = "volume_aware_mm"

    def __init__(
        self,
        quote_size: int = 1,
        half_spread_ticks: float = 1.0,
        inventory_skew_ticks: float = 0.25,
    ):
        self.quote_size = quote_size
        self.half_spread_ticks = half_spread_ticks
        self.inventory_skew_ticks = inventory_skew_ticks

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        if ctx.weekly_min_remaining <= 0:
            return None

        # Only pad volume in low-vol regimes
        if ctx.realized_vol_ticks_1h > 4.0:
            return None

        imb = ctx.extra.get("book_imbalance", 0.0)  # [-1, 1]
        skew = -ctx.position * self.inventory_skew_ticks

        if imb > 0.15:
            side = Side.SELL
        elif imb < -0.15:
            side = Side.BUY
        else:
            return None

        return Signal(
            side=side,
            size=min(self.quote_size, 10 - abs(ctx.position)),
            urgency="passive",
            reason="volume_pad_passive",
            expected_edge_ticks=-0.1,  # negative edge acceptable for min volume
            max_hold_seconds=120,
        )
