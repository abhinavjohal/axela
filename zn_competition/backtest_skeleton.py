"""
Minimal backtest harness — plug in TT exported tick/trade CSV or time bars.
Columns expected: timestamp, bid, ask, mid (or last), optional volume.
"""

from __future__ import annotations

import csv
from pathlib import Path

from zn_competition.economics import FeeDrag
from zn_competition.risk import RiskState, clip_size
from zn_competition.specs import FEE_PER_LOT_USD, WEEKLY_VOLUME_MIN, ZN_SEP26
from zn_competition.strategies.base import Side, StrategyContext
from zn_competition.strategies.macro_event import MacroEventStrategy
from zn_competition.strategies.session_mr import SessionMeanReversionStrategy
from zn_competition.strategies.volume_aware_mm import VolumeAwareMarketMaking


def load_bars(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def run_paper(
    bars: list[dict],
    week: int = 1,
) -> dict:
    strategies = [
        MacroEventStrategy(),
        SessionMeanReversionStrategy(),
        VolumeAwareMarketMaking(),
    ]
    position = 0
    lots = 0
    gross_pnl = 0.0
    risk = RiskState()

    weekly_min = WEEKLY_VOLUME_MIN[week - 1]

    for i, row in enumerate(bars):
        mid = float(row.get("mid") or row.get("last") or 0)
        bid = float(row.get("bid", mid))
        ask = float(row.get("ask", mid))
        ctx = StrategyContext(
            mid_price=mid,
            bid=bid,
            ask=ask,
            position=position,
            week_number=week,
            lots_traded_this_week=lots,
            lots_traded_total=lots,
            weekly_min_remaining=max(0, weekly_min - lots),
            extra={"vwap_z": float(row.get("vwap_z", 0))},
        )

        if risk.halted:
            break

        signal = None
        for strat in strategies:
            signal = strat.on_tick(ctx)
            if signal:
                break

        if not signal or signal.side == Side.FLAT:
            continue

        size = clip_size(signal.size, position)
        if size <= 0:
            continue

        # Naive fill at mid +/- half spread
        fill = ask if signal.side == Side.BUY else bid
        prev_mid = float(bars[i - 1].get("mid", mid)) if i else mid
        if signal.side == Side.BUY:
            position += size
            gross_pnl -= (fill - prev_mid) * ZN_SEP26.dollars_per_point * size
        else:
            position -= size
            gross_pnl += (fill - prev_mid) * ZN_SEP26.dollars_per_point * size

        lots += size
        gross_pnl -= size * FEE_PER_LOT_USD

    fees = FeeDrag(lots)
    return {
        "lots": lots,
        "position_end": position,
        "gross_pnl_usd": round(gross_pnl, 2),
        "fees_usd": fees.total_fees_usd,
        "weekly_min": weekly_min,
        "met_volume_min": lots >= weekly_min,
    }


if __name__ == "__main__":
    print(
        "Export ticks from TT → CSV with mid,bid,ask,vwap_z → "
        "run_paper(load_bars(path))"
    )
