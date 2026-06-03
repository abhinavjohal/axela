"""
Backward-compatible entry point — delegates to zn_competition.backtest.
"""

from __future__ import annotations

from zn_competition.backtest import (
    BacktestResult,
    generate_synthetic_quotes,
    load_quotes,
    run_backtest,
    run_backtest_csv,
)

__all__ = [
    "BacktestResult",
    "generate_synthetic_quotes",
    "load_quotes",
    "run_backtest",
    "run_backtest_csv",
]

if __name__ == "__main__":
    import runpy

    runpy.run_module("zn_competition.backtest", run_name="__main__")
