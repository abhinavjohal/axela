"""
Session VWAP mean reversion — fee-aware entries on ZN tick grid.
Blocked during high-impact macro; requires positive net edge vs $1.00 RT.
"""

from __future__ import annotations

from zn_competition.specs import HIGH_IMPACT_MACRO_TAGS, ZN_SEP26
from zn_competition.strategies.base import Side, Signal, StrategyContext

MIN_NET_EDGE_TICKS_RT = ZN_SEP26.dollars_to_ticks(
    ZN_SEP26.fee_round_turn, lots=1
) + 0.25


class SessionMeanReversionStrategy:
    name = "session_mean_reversion"

    def __init__(
        self,
        entry_z: float = 1.25,
        exit_z: float = 0.35,
        size: int = 2,
        max_hold_seconds: int = 900,
        min_session_tag: str = "high_liquidity",
    ) -> None:
        if entry_z <= exit_z:
            raise ValueError("entry_z must exceed exit_z")
        if size < 1 or size > ZN_SEP26.max_position_lots:
            raise ValueError(f"size must be 1–{ZN_SEP26.max_position_lots}")
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.size = size
        self.max_hold_seconds = max_hold_seconds
        self.min_session_tag = min_session_tag

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        if ctx.event_tag in HIGH_IMPACT_MACRO_TAGS:
            return None
        if ctx.features is None:
            return None
        if ctx.features.session_tag != self.min_session_tag:
            return None
        if ctx.features.spread_ticks > 2.0:
            return None

        z = ctx.features.vwap_z
        abs_z = abs(z)

        if ctx.position != 0 and abs_z < self.exit_z:
            return Signal(
                side=Side.FLAT,
                size=abs(ctx.position),
                urgency="aggressive",
                reason="mr_exit_vwap",
                expected_edge_ticks=0.5,
                max_hold_seconds=self.max_hold_seconds,
            )

        if abs_z < self.entry_z:
            return None

        side = Side.SELL if z > 0 else Side.BUY
        lots = min(self.size, ZN_SEP26.max_position_lots - abs(ctx.position))
        if lots <= 0:
            return None

        expected_ticks = min(abs_z * 0.4, 2.5)
        signal = Signal(
            side=side,
            size=lots,
            urgency="passive",
            reason="mr_enter_vwap",
            expected_edge_ticks=expected_ticks,
            max_hold_seconds=self.max_hold_seconds,
        )
        if signal.net_edge_after_round_turn_fee_ticks(lots) < MIN_NET_EDGE_TICKS_RT:
            return None
        return signal
