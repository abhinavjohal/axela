"""
Alpha + Volume platform — independent engines per TT ADL Master Plan.

Alpha Engine: OBI Sniper @ fixed threshold, 24/7 (directional edge only).
Volume Engine: Module 4 Generator @ 30s when flat (scratch spread for legs).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zn_competition.microstructure import OrderBookSnapshot, Quote
from zn_competition.risk import FillRecord, PositionLedger
from zn_competition.specs import get_instrument_spec
from zn_competition.strategies.base import StrategyContext
from zn_competition.strategies.session_mr import (
    CHURN_GENERATOR_PERIOD_MS,
    ChurnStepResult,
    VolumeChurnerExecutionEngine,
)
from zn_competition.strategies.volume_aware_mm import (
    ExecutionStepResult,
    SniperOBIEngine,
)

# Competition-tuned sniper thresholds (do NOT lower OBI to force volume)
SNIPER_OBI_THRESHOLD_DEFAULT = 0.85
SNIPER_OBI_THRESHOLD_ALT = 0.75


@dataclass
class PlatformStepResult:
    """Combined alpha + volume step for one market update."""

    alpha: ExecutionStepResult = field(default_factory=ExecutionStepResult)
    volume: ChurnStepResult | None = None
    alpha_fills: list[FillRecord] = field(default_factory=list)
    volume_fills: list[FillRecord] = field(default_factory=list)


@dataclass
class AlphaVolumePlatform:
    """
    Live/backtest coordinator matching TT canvas architecture:

    - **Alpha:** ``SniperOBIEngine`` — OBI >= threshold, 24/7, no volume-mode OBI.
    - **Volume:** ``VolumeChurnerExecutionEngine`` — 30s generator pulse, flat only.
    - **Arbitration:** churn suppressed while OBI has working order or open trade.
    - **Guards:** ZN min combined L1 qty (200) before OBI entry.
    """

    instrument_id: str = "ZN"
    alpha: SniperOBIEngine = field(default_factory=SniperOBIEngine)
    volume: VolumeChurnerExecutionEngine = field(
        default_factory=VolumeChurnerExecutionEngine
    )
    liquidity_dropouts: int = field(default=0, init=False, repr=False)
    alpha_take_profits: int = field(default=0, init=False, repr=False)

    @classmethod
    def with_sniper_threshold(
        cls,
        threshold: float = SNIPER_OBI_THRESHOLD_DEFAULT,
        pulse_period_ms: int = CHURN_GENERATOR_PERIOD_MS,
        instrument_id: str = "ZN",
    ) -> AlphaVolumePlatform:
        spec = get_instrument_spec(instrument_id)
        return cls(
            instrument_id=spec.instrument_id,
            alpha=SniperOBIEngine(entry_threshold=threshold),
            volume=VolumeChurnerExecutionEngine(pulse_period_ms=pulse_period_ms),
        )

    def reset(self) -> None:
        self.alpha.reset()
        self.volume.reset()
        self.liquidity_dropouts = 0
        self.alpha_take_profits = 0

    def instrument_spec(self):
        return get_instrument_spec(self.instrument_id)

    def alpha_blocks_volume(self) -> bool:
        """TT ``ff_OBI_InTrade`` — OBI sleeve has priority over churn."""
        return self.alpha.blocks_volume_churn()

    def process_tick(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        ctx: StrategyContext,
        *,
        run_volume: bool = True,
    ) -> PlatformStepResult:
        out = PlatformStepResult()

        out.alpha = self.alpha.process_tick(quote, book, ledger)
        out.alpha_fills = list(out.alpha.fills)
        if out.alpha.action == "liquidity_dropout":
            self.liquidity_dropouts += 1
        elif out.alpha.action == "take_profit":
            self.alpha_take_profits += 1

        if not run_volume:
            return out

        if self.alpha_blocks_volume():
            out.volume = ChurnStepResult(
                action="obi_priority_block",
                execution_token_active=False,
            )
            return out

        if ledger.position != 0:
            out.volume = ChurnStepResult(action="token_dropped")
            return out

        out.volume = self.volume.process_generator_pulse(quote, book, ledger, ctx)
        out.volume_fills = list(out.volume.fills)
        return out
