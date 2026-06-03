"""
Competition PnL vs volume economics — fees at $0.50/side, $1.00 round-turn.

Canonical trade P&L: net_pnl_from_tick_move()
"""

from __future__ import annotations

from dataclasses import dataclass

from zn_competition.specs import (
    DOLLARS_PER_TICK,
    FEE_PER_LOT_PER_SIDE_USD,
    FEE_PER_LOT_ROUND_TURN_USD,
    MAX_POSITION_LOTS,
    TOTAL_VOLUME_MIN,
    WEEKLY_VOLUME_MIN,
    ZN_SEP26,
)


def validate_trade_lots(
    lots: int,
    *,
    position_before: int = 0,
    signed_lot_delta: int | None = None,
) -> None:
    """
    Enforce competition position cap (10 lots absolute).

    Parameters
    ----------
    lots:
        Contracts in the order (must be 1–10).
    position_before:
        Signed position before the order (+ long, − short).
    signed_lot_delta:
        Change in position from this order (+buy size, −sell size).
        When provided, checks |position_before + signed_lot_delta| <= 10.
    """
    if isinstance(lots, bool) or not isinstance(lots, int):
        raise TypeError(f"lots must be int, got {type(lots).__name__}")
    if lots < 1 or lots > MAX_POSITION_LOTS:
        raise ValueError(
            f"lots must be between 1 and {MAX_POSITION_LOTS} (inclusive), got {lots}"
        )
    if isinstance(position_before, bool) or not isinstance(position_before, int):
        raise TypeError(
            f"position_before must be int, got {type(position_before).__name__}"
        )
    if abs(position_before) > MAX_POSITION_LOTS:
        raise ValueError(
            f"|position_before| cannot exceed {MAX_POSITION_LOTS}, got {position_before}"
        )
    if signed_lot_delta is None:
        return
    if not isinstance(signed_lot_delta, int) or isinstance(signed_lot_delta, bool):
        raise TypeError("signed_lot_delta must be int")
    if abs(signed_lot_delta) != lots:
        raise ValueError(
            f"|signed_lot_delta| must equal lots ({lots}), got {signed_lot_delta}"
        )
    projected = position_before + signed_lot_delta
    if abs(projected) > MAX_POSITION_LOTS:
        raise ValueError(
            f"order breaches position cap: {position_before} -> {projected} "
            f"(max absolute position {MAX_POSITION_LOTS})"
        )


@dataclass(frozen=True)
class TradePnLBreakdown:
    """Exact ZN Sep26 P&L from a tick price move after per-side fees."""

    tick_move: float
    lots: int
    sides: int
    gross_pnl_usd: float
    fees_usd: float
    net_pnl_usd: float
    dollars_per_tick: float = DOLLARS_PER_TICK
    fee_per_side_usd: float = FEE_PER_LOT_PER_SIDE_USD

    @property
    def fee_per_lot_round_turn_usd(self) -> float:
        return self.sides * self.fee_per_side_usd


def net_pnl_from_tick_move(
    tick_move: float,
    lots: int,
    *,
    sides: int = 2,
    position_before: int = 0,
    signed_lot_delta: int | None = None,
) -> TradePnLBreakdown:
    """
    Net P&L in USD for a ZN Sep26 move expressed in ticks.

    Pricing
    -------
    - Tick size: 1/64 point (0.015625)
    - Tick value: $15.625 per contract per tick
    - Fee: $0.50 per lot per exchange side
    - Default ``sides=2``: round-turn (entry + exit) → $1.00/lot total fees

    Parameters
    ----------
    tick_move:
        Signed move in ticks. Positive = favorable to a **long**
        (ZN price up). For a short, pass the negated move.
    lots:
        Contracts traded (1–10). Validated against the 10-lot position cap.
    sides:
        Exchange legs charged at $0.50/lot each: ``1`` (single fill) or ``2`` (RT).
    position_before:
        Signed position before the trade; used with ``signed_lot_delta``.
    signed_lot_delta:
        Position change from this trade (+lots for buy, −lots for sell).
        When set, verifies the post-trade position stays within ±10 lots.

    Returns
    -------
    TradePnLBreakdown
        gross = tick_move × $15.625 × lots
        fees  = lots × sides × $0.50
        net   = gross − fees
    """
    if not isinstance(tick_move, (int, float)) or isinstance(tick_move, bool):
        raise TypeError(f"tick_move must be numeric, got {type(tick_move).__name__}")
    if sides not in (1, 2):
        raise ValueError(f"sides must be 1 or 2, got {sides}")

    validate_trade_lots(
        lots,
        position_before=position_before,
        signed_lot_delta=signed_lot_delta,
    )

    gross = ZN_SEP26.gross_pnl_usd(float(tick_move), lots)
    fees = ZN_SEP26.fee_for_legs(lots, sides=sides)
    net = gross - fees

    return TradePnLBreakdown(
        tick_move=float(tick_move),
        lots=lots,
        sides=sides,
        gross_pnl_usd=gross,
        fees_usd=fees,
        net_pnl_usd=net,
    )


@dataclass(frozen=True)
class VolumePlan:
    week: int
    zn_lots: int
    other_instrument_lots: int

    def __post_init__(self) -> None:
        if self.week < 1 or self.week > 4:
            raise ValueError(f"week must be 1–4, got {self.week}")
        if self.zn_lots < 0 or self.other_instrument_lots < 0:
            raise ValueError("lot counts must be non-negative")

    @property
    def total_lots(self) -> int:
        return self.zn_lots + self.other_instrument_lots


@dataclass(frozen=True)
class FeeAccounting:
    """Fee drag for a set of exchange legs (each fill = one leg per lot)."""

    leg_lots: int
    fee_per_side: float = FEE_PER_LOT_PER_SIDE_USD

    def __post_init__(self) -> None:
        if self.leg_lots < 0:
            raise ValueError(f"leg_lots must be non-negative, got {self.leg_lots}")

    @property
    def total_fees_usd(self) -> float:
        return self.leg_lots * self.fee_per_side

    @property
    def round_turn_equivalent_lots(self) -> float:
        return self.leg_lots / 2.0

    @property
    def total_fees_if_all_round_turns_usd(self) -> float:
        return self.round_turn_equivalent_lots * FEE_PER_LOT_ROUND_TURN_USD

    def breakeven_ticks_per_leg_lot(self) -> float:
        if self.leg_lots == 0:
            return 0.0
        return self.total_fees_usd / DOLLARS_PER_TICK / self.leg_lots

    def breakeven_ticks_per_round_turn(self, lots_per_rt: int = 1) -> float:
        if lots_per_rt <= 0:
            raise ValueError(f"lots_per_rt must be positive, got {lots_per_rt}")
        rt_fee = lots_per_rt * FEE_PER_LOT_ROUND_TURN_USD
        return rt_fee / DOLLARS_PER_TICK / lots_per_rt

    def gross_ticks_needed_for_net_zero(self, leg_lots: int | None = None) -> float:
        lots = self.leg_lots if leg_lots is None else leg_lots
        if lots <= 0:
            return 0.0
        return self.fee_per_side / DOLLARS_PER_TICK


def analyze_week_plan(week: int, zn_legs: int, other_legs: int = 0) -> dict[str, float | int | bool]:
    required = WEEKLY_VOLUME_MIN[week - 1]
    total_legs = zn_legs + other_legs
    fees = FeeAccounting(total_legs)
    return {
        "week": week,
        "required_min_legs": required,
        "zn_legs": zn_legs,
        "other_legs": other_legs,
        "total_legs": total_legs,
        "meets_minimum": total_legs >= required,
        "total_fees_usd": fees.total_fees_usd,
        "breakeven_ticks_per_leg": fees.breakeven_ticks_per_leg_lot(),
        "breakeven_ticks_per_rt_1lot": fees.breakeven_ticks_per_round_turn(1),
    }


def week1_scenarios() -> list[dict[str, float | int]]:
    min_w1 = WEEKLY_VOLUME_MIN[0]
    rows: list[dict[str, float | int]] = []
    for zn_legs in (200, 150, 100, 50):
        other = max(0, min_w1 - zn_legs)
        fees = FeeAccounting(zn_legs + other)
        rows.append(
            {
                "zn_legs": zn_legs,
                "other_legs": other,
                "total_fees_usd": fees.total_fees_usd,
                "breakeven_ticks_per_leg": round(fees.breakeven_ticks_per_leg_lot(), 6),
                "breakeven_ticks_per_rt": round(fees.breakeven_ticks_per_round_turn(1), 6),
            }
        )
    return rows


def four_week_fee_budget(zn_leg_counts: list[int] | None = None) -> dict[str, object]:
    if zn_leg_counts is None:
        zn_leg_counts = list(WEEKLY_VOLUME_MIN) + [TOTAL_VOLUME_MIN - sum(WEEKLY_VOLUME_MIN)]
    if len(zn_leg_counts) != 5:
        raise ValueError("zn_leg_counts must have 5 entries (4 weeks + top-up)")
    if sum(zn_leg_counts) < TOTAL_VOLUME_MIN:
        raise ValueError(f"total legs {sum(zn_leg_counts)} below minimum {TOTAL_VOLUME_MIN}")
    fees = FeeAccounting(sum(zn_leg_counts))
    return {
        "weekly_leg_breakdown": zn_leg_counts,
        "total_legs": fees.leg_lots,
        "total_fees_usd": fees.total_fees_usd,
        "breakeven_ticks_per_leg": fees.breakeven_ticks_per_leg_lot(),
        "breakeven_ticks_per_rt_1lot": fees.breakeven_ticks_per_round_turn(1),
        "fee_round_turn_per_lot_usd": FEE_PER_LOT_ROUND_TURN_USD,
        "dollars_per_tick": DOLLARS_PER_TICK,
    }


def minimum_gross_ticks_for_target_net(
    target_net_usd: float,
    lots: int,
    *,
    sides: int = 2,
) -> float:
    """Tick move required so net_pnl_from_tick_move() equals target_net_usd."""
    validate_trade_lots(lots)
    fees = ZN_SEP26.fee_for_legs(lots, sides=sides)
    required_gross_usd = target_net_usd + fees
    return required_gross_usd / (DOLLARS_PER_TICK * lots)


if __name__ == "__main__":
    print("=== Week 1 leg scenarios (min 200 legs) ===")
    for row in week1_scenarios():
        print(row)
    print("\n=== 4-week fee budget (2000 legs on ZN) ===")
    print(four_week_fee_budget())
    print("\n=== Trade P&L examples ===")
    print(net_pnl_from_tick_move(1.0, 1))  # +1 tick, 1 lot, round-turn
    print(net_pnl_from_tick_move(3.0, 5))
    try:
        net_pnl_from_tick_move(1.0, 11)
    except ValueError as exc:
        print(f"cap rejected: {exc}")
