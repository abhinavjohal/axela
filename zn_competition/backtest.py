"""
Production backtest engine for TT-exported ZN quote CSV.
Stdlib only: csv, pathlib, dataclasses.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from zn_competition.economics import FeeAccounting, analyze_week_plan
from zn_competition.execution import execute_signal
from zn_competition.features import MicrostructureFeatureEngine
from zn_competition.microstructure import Quote
from zn_competition.risk import PositionLedger, RiskState
from zn_competition.specs import WEEKLY_VOLUME_MIN, weekly_volume_requirement
from zn_competition.strategies.base import StrategyContext
from zn_competition.strategies.engine import StrategyStack

REQUIRED_COLUMNS = frozenset({"timestamp", "bid", "ask"})
OPTIONAL_COLUMNS = frozenset(
    {
        "bid_size",
        "ask_size",
        "last",
        "volume",
        "event_tag",
        "event_phase",
        "surprise_10y_equiv_bp",
    }
)


@dataclass(frozen=True)
class BacktestResult:
    week: int
    leg_lots_traded: int
    weekly_min_legs: int
    met_volume_min: bool
    position_end: int
    realized_pnl_usd: float
    total_fees_usd: float
    mark_pnl_usd: float
    net_pnl_usd: float
    fill_count: int
    halted: bool
    halt_reason: str
    signals_by_reason: dict[str, int]

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)


def _parse_float(row: dict[str, str], key: str, default: float | None = None) -> float:
    raw = row.get(key, "").strip()
    if not raw:
        if default is not None:
            return default
        raise ValueError(f"missing required numeric column '{key}'")
    try:
        return float(raw)
    except ValueError as exc:
        raise ValueError(f"invalid float for '{key}': {raw}") from exc


def _parse_int(row: dict[str, str], key: str, default: int = 0) -> int:
    raw = row.get(key, "").strip()
    if not raw:
        return default
    try:
        return int(float(raw))
    except ValueError as exc:
        raise ValueError(f"invalid int for '{key}': {raw}") from exc


def load_quotes(path: Path) -> list[Quote]:
    if not path.is_file():
        raise FileNotFoundError(f"quote file not found: {path}")
    quotes: list[Quote] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("CSV has no header row")
        headers = {h.strip() for h in reader.fieldnames}
        missing = REQUIRED_COLUMNS - headers
        if missing:
            raise ValueError(f"CSV missing required columns: {sorted(missing)}")
        for line_no, row in enumerate(reader, start=2):
            try:
                last_raw = row.get("last", "").strip()
                quotes.append(
                    Quote(
                        timestamp=row["timestamp"].strip(),
                        bid=_parse_float(row, "bid"),
                        ask=_parse_float(row, "ask"),
                        bid_size=_parse_int(row, "bid_size", 10),
                        ask_size=_parse_int(row, "ask_size", 10),
                        last=float(last_raw) if last_raw else None,
                        volume=_parse_int(row, "volume", 1),
                    )
                )
            except (ValueError, KeyError) as exc:
                raise ValueError(f"row {line_no}: {exc}") from exc
    if not quotes:
        raise ValueError(f"no quotes loaded from {path}")
    return quotes


def _row_event_fields(row: dict[str, str]) -> tuple[str | None, str | None, float | None]:
    tag = row.get("event_tag", "").strip() or None
    phase = row.get("event_phase", "").strip() or None
    surprise_raw = row.get("surprise_10y_equiv_bp", "").strip()
    surprise = float(surprise_raw) if surprise_raw else None
    return tag, phase, surprise


def run_backtest(
    quotes: list[Quote],
    week: int = 1,
    stack: StrategyStack | None = None,
    daily_loss_limit_usd: float = 1_500.0,
    event_rows: list[dict[str, str]] | None = None,
) -> BacktestResult:
    if week < 1 or week > 4:
        raise ValueError(f"week must be 1–4, got {week}")
    if not quotes:
        raise ValueError("quotes list is empty")

    weekly_min = weekly_volume_requirement(week)
    engine = MicrostructureFeatureEngine()
    strategy_stack = stack or StrategyStack()
    ledger = PositionLedger()
    risk = RiskState(daily_loss_limit_usd=daily_loss_limit_usd)
    signals_by_reason: dict[str, int] = {}

    for idx, quote in enumerate(quotes):
        if risk.halted:
            break

        features = engine.update(quote)
        tag, phase, surprise = (None, None, None)
        if event_rows and idx < len(event_rows):
            tag, phase, surprise = _row_event_fields(event_rows[idx])

        ctx = StrategyContext(
            mid_price=features.mid,
            bid=quote.bid,
            ask=quote.ask,
            position=ledger.position,
            week_number=week,
            leg_lots_traded_this_week=ledger.leg_lots_traded,
            leg_lots_traded_total=ledger.leg_lots_traded,
            weekly_min_remaining=max(0, weekly_min - ledger.leg_lots_traded),
            features=features,
            event_tag=tag,
            event_phase=phase,
            surprise_10y_equiv_bp=surprise,
        )

        signal = strategy_stack.on_tick(ctx)
        if signal is None:
            continue

        signals_by_reason[signal.reason] = signals_by_reason.get(signal.reason, 0) + 1
        fill = execute_signal(signal, quote, ledger.position)
        if fill is None:
            continue

        net = ledger.apply_fill(fill)
        risk.apply_realized(net)

    mark = quotes[-1].mid
    mark_pnl = ledger.mark_price_pnl_usd(mark)
    net_total = ledger.realized_pnl_usd + mark_pnl

    return BacktestResult(
        week=week,
        leg_lots_traded=ledger.leg_lots_traded,
        weekly_min_legs=weekly_min,
        met_volume_min=ledger.leg_lots_traded >= weekly_min,
        position_end=ledger.position,
        realized_pnl_usd=round(ledger.realized_pnl_usd, 2),
        total_fees_usd=round(ledger.total_fees_usd, 2),
        mark_pnl_usd=round(mark_pnl, 2),
        net_pnl_usd=round(net_total, 2),
        fill_count=len(ledger.fills),
        halted=risk.halted,
        halt_reason=risk.halt_reason,
        signals_by_reason=signals_by_reason,
    )


def run_backtest_csv(path: Path, week: int = 1) -> BacktestResult:
    quotes = load_quotes(path)
    event_rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames:
            for row in reader:
                event_rows.append(row)
    return run_backtest(quotes, week=week, event_rows=event_rows)


def generate_synthetic_quotes(count: int = 500, base_price: float = 112.0) -> list[Quote]:
    """Deterministic synthetic stream for offline validation (no external data)."""
    if count < 10:
        raise ValueError("count must be >= 10")
    quotes: list[Quote] = []
    mid = base_price
    for i in range(count):
        drift = ((i % 17) - 8) * (1 / 64) * 0.1
        mid = max(100.0, mid + drift)
        half_spread = 1 / 128
        bid = mid - half_spread
        ask = mid + half_spread
        quotes.append(
            Quote(
                timestamp=f"2026-06-03T14:{i % 60:02d}:00+00:00",
                bid=round(bid, 6),
                ask=round(ask, 6),
                bid_size=20 + (i % 5),
                ask_size=18 + (i % 7),
                volume=1 + (i % 3),
            )
        )
    return quotes


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        result = run_backtest_csv(Path(sys.argv[1]), week=int(sys.argv[2]) if len(sys.argv) > 2 else 1)
    else:
        synthetic = generate_synthetic_quotes()
        result = run_backtest(synthetic, week=1)
    print(result.to_json())
    plan = analyze_week_plan(1, result.leg_lots_traded)
    print("week_plan:", plan)
    print("fee_accounting:", asdict(FeeAccounting(result.leg_lots_traded)))
