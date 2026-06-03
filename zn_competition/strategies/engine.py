"""
Strategy stack with regime state machine and execution-layer order purge.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from zn_competition.execution import ExecutionEngine, StrategyRegime, PurgeReport
from zn_competition.microstructure import Quote
from zn_competition.risk import PositionLedger
from zn_competition.strategies.base import Signal, Strategy, StrategyContext
from zn_competition.strategies.macro_event import MacroEventStrategy
from zn_competition.strategies.session_mr import SessionMeanReversionStrategy
from zn_competition.strategies.volume_aware_mm import VolumeAwareMarketMaking

logger = logging.getLogger(__name__)

TREND_FOLLOWING_STRATEGIES: frozenset[str] = frozenset({"macro_event"})
MEAN_REVERSION_STRATEGIES: frozenset[str] = frozenset(
    {"session_mean_reversion", "volume_aware_mm", "order_book_imbalance_hft"}
)


class StrategyState(str, Enum):
    """High-level state machine labels exposed to operators."""

    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"
    IDLE = "idle"


def regime_for_strategy(strategy_name: str) -> StrategyRegime:
    if strategy_name in TREND_FOLLOWING_STRATEGIES:
        return StrategyRegime.TREND_FOLLOWING
    if strategy_name in MEAN_REVERSION_STRATEGIES:
        return StrategyRegime.MEAN_REVERSION
    return StrategyRegime.NEUTRAL


@dataclass
class StrategyTickResult:
    signal: Signal | None
    strategy_name: str | None
    regime: StrategyRegime
    state: StrategyState
    regime_changed: bool
    purge_report: PurgeReport | None = None


class StrategyStack:
    """
    Priority stack with regime tracking.

    On transition **Trend-Following → Mean-Reversion**, all active working orders
    from the opposing (trend) regime are purged via ``ExecutionEngine``.
    """

    def __init__(
        self,
        strategies: list[Strategy] | None = None,
        execution: ExecutionEngine | None = None,
    ) -> None:
        self.execution = execution or ExecutionEngine()
        self._strategies = strategies or [
            MacroEventStrategy(),
            SessionMeanReversionStrategy(),
            VolumeAwareMarketMaking(),
        ]
        if not self._strategies:
            raise ValueError("strategy stack cannot be empty")
        self._active_regime = StrategyRegime.NEUTRAL
        self._active_state = StrategyState.IDLE
        self._last_signal_strategy: str | None = None

    @property
    def names(self) -> list[str]:
        return [s.name for s in self._strategies]

    @property
    def active_regime(self) -> StrategyRegime:
        return self._active_regime

    @property
    def active_state(self) -> StrategyState:
        return self._active_state

    def _state_from_regime(self, regime: StrategyRegime) -> StrategyState:
        if regime == StrategyRegime.TREND_FOLLOWING:
            return StrategyState.TREND_FOLLOWING
        if regime == StrategyRegime.MEAN_REVERSION:
            return StrategyState.MEAN_REVERSION
        return StrategyState.IDLE

    def _handle_regime_transition(
        self,
        new_regime: StrategyRegime,
        new_strategy: str,
    ) -> PurgeReport | None:
        old_regime = self._active_regime
        old_state = self._active_state
        new_state = self._state_from_regime(new_regime)

        trend_to_mr = (
            old_regime == StrategyRegime.TREND_FOLLOWING
            and new_regime == StrategyRegime.MEAN_REVERSION
        )
        mr_to_trend = (
            old_regime == StrategyRegime.MEAN_REVERSION
            and new_regime == StrategyRegime.TREND_FOLLOWING
        )

        purge: PurgeReport | None = None
        if trend_to_mr:
            purge = self.execution.purge_regime(StrategyRegime.TREND_FOLLOWING)
            logger.warning(
                "REGIME_FLIP trend_following -> mean_reversion: purged %d trend orders",
                purge.canceled_count,
            )
        elif mr_to_trend:
            purge = self.execution.purge_regime(StrategyRegime.MEAN_REVERSION)
            logger.warning(
                "REGIME_FLIP mean_reversion -> trend_following: purged %d MR orders",
                purge.canceled_count,
            )

        if new_regime != old_regime or new_state != old_state:
            logger.info(
                "REGIME_CHANGE %s -> %s (strategy=%s)",
                old_regime.value,
                new_regime.value,
                new_strategy,
            )

        self._active_regime = new_regime
        self._active_state = new_state
        self._last_signal_strategy = new_strategy
        return purge

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        result = self.evaluate_tick(ctx)
        return result.signal

    def evaluate_tick(self, ctx: StrategyContext) -> StrategyTickResult:
        """Evaluate strategies; purge opposing working orders on regime flip."""
        previous_regime = self._active_regime
        signal: Signal | None = None
        winner: Strategy | None = None

        for strategy in self._strategies:
            candidate = strategy.on_tick(ctx)
            if candidate is not None:
                signal = candidate
                winner = strategy
                break

        if winner is None or signal is None:
            return StrategyTickResult(
                signal=None,
                strategy_name=None,
                regime=self._active_regime,
                state=self._active_state,
                regime_changed=False,
            )

        new_regime = regime_for_strategy(winner.name)
        regime_changed = new_regime != previous_regime
        purge_report = self._handle_regime_transition(new_regime, winner.name)

        return StrategyTickResult(
            signal=signal,
            strategy_name=winner.name,
            regime=new_regime,
            state=self._active_state,
            regime_changed=regime_changed,
            purge_report=purge_report,
        )

    def process_tick(
        self,
        ctx: StrategyContext,
        quote: Quote,
        ledger: PositionLedger,
    ) -> StrategyTickResult:
        """
        Full path: evaluate → purge on flip → submit through execution engine.
        """
        result = self.evaluate_tick(ctx)
        if result.signal is None:
            return result

        fill = self.execution.submit_signal(
            ledger,
            result.signal,
            quote,
            regime=result.regime,
            strategy_name=result.strategy_name or "",
        )
        if fill is None:
            result.signal = None
        return result
