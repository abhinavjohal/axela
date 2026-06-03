"""
Priority strategy stack — macro > mean reversion > volume pad.
"""

from __future__ import annotations

from zn_competition.strategies.base import Signal, Strategy, StrategyContext
from zn_competition.strategies.macro_event import MacroEventStrategy
from zn_competition.strategies.session_mr import SessionMeanReversionStrategy
from zn_competition.strategies.volume_aware_mm import VolumeAwareMarketMaking


class StrategyStack:
    def __init__(self, strategies: list[Strategy] | None = None) -> None:
        self._strategies: list[Strategy] = strategies or [
            MacroEventStrategy(),
            SessionMeanReversionStrategy(),
            VolumeAwareMarketMaking(),
        ]
        if not self._strategies:
            raise ValueError("strategy stack cannot be empty")

    @property
    def names(self) -> list[str]:
        return [s.name for s in self._strategies]

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        for strategy in self._strategies:
            signal = strategy.on_tick(ctx)
            if signal is not None:
                return signal
        return None
