"""
Competition PnL vs volume economics — fees at $0.50/side, $1.00 round-turn.
"""

from __future__ import annotations

from dataclasses import dataclass

from zn_competition.specs import (
    DOLLARS_PER_TICK,
    FEE_PER_LOT_PER_SIDE_USD,
    FEE_PER_LOT_ROUND_TURN_USD,
    TOTAL_VOLUME_MIN,
    WEEKLY_VOLUME_MIN,
    ZN_SEP26,
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
    leg_lots: int,
) -> float:
    """Gross ticks required across leg_lots so net = target after $0.50/side fees."""
    if leg_lots <= 0:
        raise ValueError(f"leg_lots must be positive, got {leg_lots}")
    fees = ZN_SEP26.fee_for_legs(leg_lots, 1)
    required_gross_usd = target_net_usd + fees
    return ZN_SEP26.dollars_to_ticks(required_gross_usd, lots=leg_lots)


if __name__ == "__main__":
    print("=== Week 1 leg scenarios (min 200 legs) ===")
    for row in week1_scenarios():
        print(row)
    print("\n=== 4-week fee budget (2000 legs on ZN) ===")
    print(four_week_fee_budget())
