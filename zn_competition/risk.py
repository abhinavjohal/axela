"""Hard risk limits aligned to competition rules."""

from __future__ import annotations

from dataclasses import dataclass

from zn_competition.specs import MAX_POSITION_LOTS


@dataclass
class RiskState:
    position: int = 0
    daily_loss_limit_usd: float = 1_500.0
    daily_pnl_usd: float = 0.0
    halted: bool = False


def clip_size(requested: int, position: int) -> int:
    cap = MAX_POSITION_LOTS - abs(position)
    return max(0, min(requested, cap, MAX_POSITION_LOTS))


def check_daily_stop(state: RiskState) -> bool:
    if state.daily_pnl_usd <= -state.daily_loss_limit_usd:
        state.halted = True
    return state.halted
