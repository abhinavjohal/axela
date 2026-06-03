from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Protocol

from zn_competition.features import FeatureSnapshot
from zn_competition.microstructure import OrderBookSnapshot
from zn_competition.specs import FEE_PER_LOT_ROUND_TURN_USD, ZN_SEP26


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


@dataclass(frozen=True)
class Signal:
    side: Side
    size: int
    urgency: str
    reason: str
    expected_edge_ticks: float
    max_hold_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.size < 0:
            raise ValueError(f"size must be non-negative, got {self.size}")
        if self.urgency not in ("passive", "aggressive"):
            raise ValueError(f"urgency must be passive|aggressive, got {self.urgency}")
        if self.side != Side.FLAT and self.size == 0:
            raise ValueError("BUY/SELL signals require positive size")

    def net_edge_after_round_turn_fee_ticks(self, lots: int = 1) -> float:
        """Expected edge minus $1.00/lot round-turn fee expressed in ticks."""
        if lots <= 0:
            raise ValueError(f"lots must be positive, got {lots}")
        fee_ticks = ZN_SEP26.dollars_to_ticks(FEE_PER_LOT_ROUND_TURN_USD * lots, lots=lots)
        return self.expected_edge_ticks - fee_ticks


@dataclass(frozen=True)
class StrategyContext:
    mid_price: float
    bid: float
    ask: float
    position: int
    week_number: int
    leg_lots_traded_this_week: int
    leg_lots_traded_total: int
    weekly_min_remaining: int
    features: FeatureSnapshot | None = None
    book: OrderBookSnapshot | None = None
    bid_l1_size: int = 0
    ask_l1_size: int = 0
    bid_l2_size: int = 0
    ask_l2_size: int = 0
    event_tag: str | None = None
    event_phase: str | None = None
    surprise_10y_equiv_bp: float | None = None

    def __post_init__(self) -> None:
        if self.week_number < 1 or self.week_number > 4:
            raise ValueError(f"week_number must be 1–4, got {self.week_number}")
        if abs(self.position) > ZN_SEP26.max_position_lots:
            raise ValueError(f"position {self.position} exceeds cap")


class Strategy(Protocol):
    name: str

    def on_tick(self, ctx: StrategyContext) -> Signal | None: ...
