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
    DEFAULT_ZN_MIN_DATA_PATH,
    HistoricalSummary,
    HistoricalSimulator,
    generate_mock_order_book_stream,
    load_zn_min_csv,
    print_historical_summary,
    row_to_level1_dict,
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
    "load_zn_min_csv",
    "print_historical_summary",
    "run_backtest",
    "run_backtest_csv",
    "run_historical_loop",
]

if __name__ == "__main__":
    import sys
    from pathlib import Path

    if len(sys.argv) > 1 and sys.argv[1] == "--csv":
        path = sys.argv[2] if len(sys.argv) > 2 else str(DEFAULT_ZN_MIN_DATA_PATH)
        result = run_backtest_csv(Path(path), week=int(sys.argv[3]) if len(sys.argv) > 3 else 1)
        print(result.to_json())
    elif len(sys.argv) > 1 and sys.argv[1] == "--backtest":
        path = sys.argv[2] if len(sys.argv) > 2 else None
        result = run_backtest(csv_path=path, week=int(sys.argv[3]) if len(sys.argv) > 3 else 1)
        print(result.to_json())
    else:
        summary = run_historical_loop()
        print_historical_summary(summary)
