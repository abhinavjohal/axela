from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    FLAT = "FLAT"


@dataclass
class Signal:
    side: Side
    size: int  # lots, capped externally at 10
    urgency: str = "passive"  # passive | aggressive
    reason: str = ""
    expected_edge_ticks: float = 0.0
    max_hold_seconds: int | None = None


@dataclass
class StrategyContext:
    mid_price: float
    bid: float
    ask: float
    position: int
    week_number: int
    lots_traded_this_week: int
    lots_traded_total: int
    weekly_min_remaining: int
    session_tag: str = ""
    event_tag: str | None = None
    realized_vol_ticks_1h: float = 0.0
    extra: dict = field(default_factory=dict)


class Strategy(Protocol):
    name: str

    def on_tick(self, ctx: StrategyContext) -> Signal | None: ...
