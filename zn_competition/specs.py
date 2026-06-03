"""
CME 10-Year T-Note (ZN) Sep 2026 — competition contract constants.
All PnL and fee math in this package must reference these values.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from fractions import Fraction
from typing import Sequence
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
IST = ZoneInfo("Asia/Kolkata")

COMPETITION_START_IST = "2026-06-01 03:30:00+05:30"
COMPETITION_END_IST = "2026-06-27 03:30:00+05:30"

WEEKLY_VOLUME_MIN: tuple[int, ...] = (200, 300, 400, 500)
TOTAL_VOLUME_MIN = 2000
MAX_POSITION_LOTS = 10

# ZN Globex: half of 1/32 point = 1/64 point
TICK_SIZE = Fraction(1, 64)
TICK_SIZE_FLOAT = float(TICK_SIZE)  # 0.015625

# $1,000 per full point × 1/64 point per tick
DOLLARS_PER_POINT = 1000.0
DOLLARS_PER_TICK = DOLLARS_PER_POINT * TICK_SIZE_FLOAT  # 15.625


def price_delta_to_ticks(price_delta: float) -> float:
    """Convert a ZN price difference to ticks (uses tick size 0.015625)."""
    return price_delta / TICK_SIZE_FLOAT


def ticks_to_price_delta(ticks: float) -> float:
    """Convert tick count to ZN price space."""
    return ticks * TICK_SIZE_FLOAT

FEE_PER_LOT_PER_SIDE_USD = 0.50
FEE_PER_LOT_ROUND_TURN_USD = 1.00

# Backward-compatible alias (one exchange leg)
FEE_PER_LOT_USD = FEE_PER_LOT_PER_SIDE_USD


@dataclass(frozen=True)
class ZNContract:
    symbol: str = "ZN"
    month_code: str = "U"
    year: int = 2026
    tt_instrument: str = "ZN Sep26"

    tick_size: Fraction = TICK_SIZE
    dollars_per_tick: float = DOLLARS_PER_TICK
    fee_per_side: float = FEE_PER_LOT_PER_SIDE_USD
    fee_round_turn: float = FEE_PER_LOT_ROUND_TURN_USD
    max_position_lots: int = MAX_POSITION_LOTS

    def round_price_to_tick(self, price: float) -> float:
        """Round price to nearest valid ZN tick (1/64)."""
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        ticks = round(price / TICK_SIZE_FLOAT)
        return ticks * TICK_SIZE_FLOAT

    def price_delta_to_ticks(self, price_delta: float) -> float:
        return price_delta / TICK_SIZE_FLOAT

    def gross_pnl_usd(self, tick_move: float, lots: int) -> float:
        """
        Gross P&L from a signed tick move.
        Positive tick_move = price moved in favor of a long (ZN up -> long wins).
        """
        if lots < 1:
            raise ValueError(f"lots must be >= 1, got {lots}")
        return tick_move * self.dollars_per_tick * lots

    def ticks_to_dollars(self, ticks: float, lots: int = 1) -> float:
        return self.gross_pnl_usd(ticks, lots)

    def dollars_to_ticks(self, dollars: float, lots: int = 1) -> float:
        if lots <= 0:
            raise ValueError(f"lots must be positive, got {lots}")
        return dollars / (self.dollars_per_tick * lots)

    def fee_for_legs(self, lots: int, sides: int = 1) -> float:
        """Total fees for `sides` exchange legs (1=single fill, 2=round-turn)."""
        if lots < 1:
            raise ValueError(f"lots must be >= 1, got {lots}")
        if sides < 1 or sides > 2:
            raise ValueError(f"sides must be 1 or 2, got {sides}")
        return lots * sides * self.fee_per_side

    def fee_for_round_turn(self, lots: int) -> float:
        return self.fee_for_legs(lots, sides=2)

    def fee_as_ticks(self, lots: int, sides: int = 1) -> float:
        """Per-lot fee expressed in price ticks for one leg or round-turn."""
        if lots < 1:
            raise ValueError(f"lots must be >= 1, got {lots}")
        if sides == 1:
            return self.fee_per_side / self.dollars_per_tick
        if sides == 2:
            return self.fee_round_turn / self.dollars_per_tick
        raise ValueError(f"sides must be 1 or 2, got {sides}")

    def net_pnl_usd(self, tick_move: float, lots: int, sides: int = 2) -> float:
        """Gross tick P&L minus $0.50/lot/side × sides (default 2 = round-turn)."""
        return self.gross_pnl_usd(tick_move, lots) - self.fee_for_legs(lots, sides)


ZN_SEP26 = ZNContract()

HIGH_LIQUIDITY_WINDOWS_CT: tuple[tuple[time, time], ...] = (
    (time(7, 20), time(10, 0)),
    (time(12, 0), time(15, 0)),
    (time(18, 0), time(20, 0)),
)

MACRO_EVENT_TAGS: frozenset[str] = frozenset(
    {
        "FOMC_rate_decision",
        "FOMC_minutes",
        "NFP",
        "CPI",
        "PCE",
        "ISM_manufacturing",
        "ISM_services",
        "retail_sales",
        "10Y_auction",
        "30Y_auction",
        "Fed_speakers",
        "jobless_claims",
    }
)

HIGH_IMPACT_MACRO_TAGS: frozenset[str] = frozenset(
    {"FOMC_rate_decision", "NFP", "CPI", "10Y_auction", "30Y_auction"}
)


def week_index_for_date(dt: datetime, competition_start: datetime) -> int:
    """Return 1–4 for competition week; raises if before start."""
    if dt.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    delta_days = (dt.date() - competition_start.date()).days
    if delta_days < 0:
        raise ValueError("date is before competition start")
    return min(4, delta_days // 7 + 1)


def weekly_volume_requirement(week: int) -> int:
    if week < 1 or week > 4:
        raise ValueError(f"week must be 1–4, got {week}")
    return WEEKLY_VOLUME_MIN[week - 1]


def in_liquidity_window(t: time, windows: Sequence[tuple[time, time]] = HIGH_LIQUIDITY_WINDOWS_CT) -> bool:
    for start, end in windows:
        if start <= end:
            if start <= t <= end:
                return True
        elif t >= start or t <= end:
            return True
    return False
