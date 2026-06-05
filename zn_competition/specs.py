"""
Competition contract constants — ZN Sep26 master specification.

All PnL and fee math must reference ``get_instrument_spec()`` / ``ZN_SEP26``.
Fee: $0.50/lot/side ($1.00 round-turn).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time
from fractions import Fraction
from pathlib import Path
from typing import Sequence
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
IST = ZoneInfo("Asia/Kolkata")

COMPETITION_START_IST = "2026-06-01 03:30:00+05:30"
COMPETITION_END_IST = "2026-06-27 03:30:00+05:30"

WEEKLY_VOLUME_MIN: tuple[int, ...] = (200, 300, 400, 500)
TOTAL_VOLUME_MIN = 2000
MAX_POSITION_LOTS = 10

FEE_PER_LOT_PER_SIDE_USD = 0.50
FEE_PER_LOT_ROUND_TURN_USD = 1.00
FEE_PER_LOT_USD = FEE_PER_LOT_PER_SIDE_USD


@dataclass(frozen=True)
class InstrumentSpec:
    """Per-instrument tick grid, tick value, and shared competition fee schedule."""

    instrument_id: str
    symbol: str
    tick_size: float
    tick_value: float
    fee_per_side: float = FEE_PER_LOT_PER_SIDE_USD
    fee_round_turn: float = FEE_PER_LOT_ROUND_TURN_USD
    max_position_lots: int = MAX_POSITION_LOTS
    tt_instrument: str = ""
    min_combined_volume: int = 0
    min_alpha_profit_ticks: int = 0

    @property
    def tick_size_float(self) -> float:
        return self.tick_size

    @property
    def dollars_per_tick(self) -> float:
        return self.tick_value

    def round_price_to_tick(self, price: float) -> float:
        if price <= 0:
            raise ValueError(f"price must be positive, got {price}")
        ticks = round(price / self.tick_size)
        return ticks * self.tick_size

    def price_delta_to_ticks(self, price_delta: float) -> float:
        return price_delta / self.tick_size

    def ticks_to_price_delta(self, ticks: float) -> float:
        return ticks * self.tick_size

    def gross_pnl_usd(self, tick_move: float, lots: int) -> float:
        if lots < 1:
            raise ValueError(f"lots must be >= 1, got {lots}")
        return tick_move * self.tick_value * lots

    def ticks_to_dollars(self, ticks: float, lots: int = 1) -> float:
        return self.gross_pnl_usd(ticks, lots)

    def dollars_to_ticks(self, dollars: float, lots: int = 1) -> float:
        if lots <= 0:
            raise ValueError(f"lots must be positive, got {lots}")
        return dollars / (self.tick_value * lots)

    def fee_for_legs(self, lots: int, sides: int = 1) -> float:
        if lots < 1:
            raise ValueError(f"lots must be >= 1, got {lots}")
        if sides < 1 or sides > 2:
            raise ValueError(f"sides must be 1 or 2, got {sides}")
        return lots * sides * self.fee_per_side

    def fee_for_round_turn(self, lots: int) -> float:
        return self.fee_for_legs(lots, sides=2)

    def fee_as_ticks(self, lots: int, sides: int = 1) -> float:
        if lots < 1:
            raise ValueError(f"lots must be >= 1, got {lots}")
        if sides == 1:
            return self.fee_per_side / self.tick_value
        if sides == 2:
            return self.fee_round_turn / self.tick_value
        raise ValueError(f"sides must be 1 or 2, got {sides}")

    def net_pnl_usd(self, tick_move: float, lots: int, sides: int = 2) -> float:
        return self.gross_pnl_usd(tick_move, lots) - self.fee_for_legs(lots, sides)

    def combined_liquidity(self, direct_bid_qty: int, direct_ask_qty: int) -> int:
        return direct_bid_qty + direct_ask_qty

    def liquidity_sufficient(self, direct_bid_qty: int, direct_ask_qty: int) -> bool:
        """True when inside bid+ask qty clears the asset minimum book depth gate."""
        return self.combined_liquidity(direct_bid_qty, direct_ask_qty) >= self.min_combined_volume

    def alpha_profit_buffer_ticks(self) -> int:
        """Minimum favorable ticks before alpha may scratch (0 = immediate scratch allowed)."""
        return self.min_alpha_profit_ticks


INSTRUMENT_SPECS: dict[str, InstrumentSpec] = {
    "ZN": InstrumentSpec(
        instrument_id="ZN",
        symbol="ZN",
        tick_size=0.015625,
        tick_value=15.625,
        tt_instrument="ZN Sep26",
        min_combined_volume=200,
        min_alpha_profit_ticks=0,
    ),
}

_DATA_DIR = Path(__file__).resolve().parent / "data"
ZN_MIN_DATA_CSV = "zn_min_data.csv"
DEFAULT_MIN_DATA_CSV: dict[str, str] = {"ZN": ZN_MIN_DATA_CSV}


def get_instrument_spec(instrument_id: str) -> InstrumentSpec:
    """Resolve master spec by instrument id (case-insensitive)."""
    key = instrument_id.strip().upper()
    if key not in INSTRUMENT_SPECS:
        known = ", ".join(sorted(INSTRUMENT_SPECS))
        raise ValueError(
            f"unknown instrument_id {instrument_id!r}; expected one of: {known}"
        )
    return INSTRUMENT_SPECS[key]


def resolve_min_data_path(
    instrument_id: str = "ZN",
    path: Path | str | None = None,
) -> Path:
    """Default 1-minute CSV path for an instrument unless ``path`` is explicit."""
    if path is not None:
        return Path(path)
    spec = get_instrument_spec(instrument_id)
    filename = DEFAULT_MIN_DATA_CSV[spec.instrument_id]
    return _DATA_DIR / filename


# Backward-compatible ZN aliases (legacy imports)
TICK_SIZE = Fraction(1, 64)
TICK_SIZE_FLOAT = INSTRUMENT_SPECS["ZN"].tick_size
DOLLARS_PER_POINT = 1000.0
DOLLARS_PER_TICK = INSTRUMENT_SPECS["ZN"].tick_value
ZN_SEP26 = INSTRUMENT_SPECS["ZN"]
ZNContract = InstrumentSpec


def price_delta_to_ticks(
    price_delta: float,
    instrument_id: str = "ZN",
) -> float:
    return get_instrument_spec(instrument_id).price_delta_to_ticks(price_delta)


def ticks_to_price_delta(
    ticks: float,
    instrument_id: str = "ZN",
) -> float:
    return get_instrument_spec(instrument_id).ticks_to_price_delta(ticks)


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


def in_liquidity_window(
    t: time,
    windows: Sequence[tuple[time, time]] = HIGH_LIQUIDITY_WINDOWS_CT,
) -> bool:
    for start, end in windows:
        if start <= end:
            if start <= t <= end:
                return True
        elif t >= start or t <= end:
            return True
    return False
