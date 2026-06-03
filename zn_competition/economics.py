"""
Competition PnL vs volume economics for ZN.
Use this to size how much edge you need per lot after $0.50/lot fees.
"""

from __future__ import annotations

from dataclasses import dataclass

from zn_competition.specs import (
    FEE_PER_LOT_USD,
    TOTAL_VOLUME_MIN,
    WEEKLY_VOLUME_MIN,
    ZN_SEP26,
)


@dataclass
class VolumePlan:
    week: int
    zn_lots: int
    other_instrument_lots: int

    @property
    def total_lots(self) -> int:
        return self.zn_lots + self.other_instrument_lots


@dataclass
class FeeDrag:
    total_lots: int
    fee_per_lot: float = FEE_PER_LOT_USD

    @property
    def total_fees_usd(self) -> float:
        return self.total_lots * self.fee_per_lot

    def breakeven_ticks(self) -> float:
        """Gross ticks of PnL needed to cover fees (spread across all lots traded)."""
        if self.total_lots == 0:
            return 0.0
        return self.total_fees_usd / ZN_SEP26.dollars_per_tick / self.total_lots

    def breakeven_ticks_per_round_trip(self, contracts_per_rt: int = 1) -> float:
        """Gross ticks needed on one round trip (buy + sell volume)."""
        lots_per_rt = 2 * contracts_per_rt
        return (lots_per_rt * self.fee_per_lot) / ZN_SEP26.dollars_per_tick


def week1_scenarios() -> list[dict]:
    """Week 1 minimum = 200 lots. Illustrate ZN-only vs split with micros."""
    min_w1 = WEEKLY_VOLUME_MIN[0]
    rows = []
    for zn_share in (200, 150, 100, 50):
        zn = zn_share
        other = max(0, min_w1 - zn)
        fees = FeeDrag(zn + other)
        rows.append(
            {
                "zn_lots": zn,
                "other_lots": other,
                "total_fees_usd": fees.total_fees_usd,
                "breakeven_ticks_per_lot": round(fees.breakeven_ticks(), 4),
                "breakeven_usd_per_lot": round(
                    fees.breakeven_ticks() * ZN_SEP26.dollars_per_tick, 2
                ),
            }
        )
    return rows


def four_week_fee_budget(zn_only_lots: list[int] | None = None) -> dict:
    """
    Full competition fee drag if you hit exactly weekly mins on ZN.
    Default: all 2000 lots on ZN (worst-case fee; best if ZN is your edge).
    """
    if zn_only_lots is None:
        zn_only_lots = WEEKLY_VOLUME_MIN + [TOTAL_VOLUME_MIN - sum(WEEKLY_VOLUME_MIN)]
    total = sum(zn_only_lots)
    fees = FeeDrag(total)
    return {
        "weekly_breakdown": zn_only_lots,
        "total_lots": total,
        "total_fees_usd": fees.total_fees_usd,
        "breakeven_ticks_per_lot": fees.breakeven_ticks(),
        "breakeven_gross_pnl_usd": fees.total_fees_usd,
        "note": (
            "At 2000 lots and $0.50/lot, fee drag = $1,000 gross PnL before net. "
            "1 tick on 1 lot = $15.625 — you need ~0.032 ticks/lot average "
            "if spread evenly, or fewer high-conviction trades."
        ),
    }


if __name__ == "__main__":
    print("=== Week 1 volume scenarios (min 200 lots) ===")
    for r in week1_scenarios():
        print(r)
    print("\n=== 4-week ZN-only fee budget (2000 lots) ===")
    print(four_week_fee_budget())
