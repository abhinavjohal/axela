"""
OBI entry-threshold sweep on 1-minute historical CSV.

Loops activation thresholds from 0.50 to 0.85 (step 0.05) and prints per-run
P&L summary so you can find the net-profitable sweet spot after $0.50/side fees.
"""

from __future__ import annotations

import sys
from pathlib import Path

from zn_competition.backtest import run_backtest
from zn_competition.historical import DEFAULT_ZN_MIN_DATA_PATH, load_zn_min_csv
from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD

THRESHOLD_START = 0.50
THRESHOLD_END = 0.85
THRESHOLD_STEP = 0.05


def iter_obi_thresholds(
    start: float = THRESHOLD_START,
    end: float = THRESHOLD_END,
    step: float = THRESHOLD_STEP,
) -> list[float]:
    values: list[float] = []
    threshold = start
    while threshold <= end + 1e-9:
        values.append(round(threshold, 2))
        threshold += step
    return values


def run_obi_sweep(csv_path: Path | str | None = None) -> None:
    path = Path(csv_path) if csv_path is not None else DEFAULT_ZN_MIN_DATA_PATH
    quotes = load_zn_min_csv(path)

    print(f"OBI threshold sweep — {len(quotes)} bars from {path}")
    print()
    print(
        f"{'OBI Threshold':>14}  {'Total Lots':>10}  {'Gross P&L':>12}  "
        f"{'Commission':>12}  {'Net P&L':>12}"
    )
    print("-" * 66)

    best_threshold: float | None = None
    best_net = float("-inf")

    for threshold in iter_obi_thresholds():
        result = run_backtest(quotes=quotes, obi_entry_threshold=threshold)
        lots = result.leg_lots_traded
        gross = result.gross_pnl_usd
        fees = lots * FEE_PER_LOT_PER_SIDE_USD
        net = result.net_pnl_usd

        print(
            f"{threshold:>14.2f}  {lots:>10}  "
            f"${gross:>10,.2f}  ${fees:>10,.2f}  ${net:>10,.2f}"
        )

        if net > best_net:
            best_net = net
            best_threshold = threshold

    print()
    if best_threshold is not None:
        print(
            f"Best net P&L: OBI threshold {best_threshold:.2f} → ${best_net:,.2f}"
        )


if __name__ == "__main__":
    csv_arg = Path(sys.argv[1]) if len(sys.argv) > 1 else None
    run_obi_sweep(csv_arg)
