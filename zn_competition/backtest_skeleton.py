"""
Historical mock order-book loop and backward-compatible backtest exports.
"""

from __future__ import annotations

from zn_competition.backtest import (
    BacktestResult,
    generate_synthetic_quotes,
    load_quotes,
    run_backtest,
    run_backtest_csv,
)
from zn_competition.historical import (
    HistoricalSummary,
    HistoricalSimulator,
    generate_mock_order_book_stream,
    print_historical_summary,
    run_historical_loop,
)
from zn_competition.risk import ExecutionException

__all__ = [
    "BacktestResult",
    "ExecutionException",
    "HistoricalSimulator",
    "HistoricalSummary",
    "generate_mock_order_book_stream",
    "generate_synthetic_quotes",
    "load_quotes",
    "print_historical_summary",
    "run_backtest",
    "run_backtest_csv",
    "run_historical_loop",
]

if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == "--csv":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        if not path:
            raise SystemExit("usage: python -m zn_competition.backtest_skeleton --csv <file> [week]")
        from pathlib import Path

        result = run_backtest_csv(Path(path), week=int(sys.argv[3]) if len(sys.argv) > 3 else 1)
        print(result.to_json())
    else:
        summary = run_historical_loop()
        print_historical_summary(summary)
