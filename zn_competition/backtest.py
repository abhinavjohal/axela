"""
Production backtest engine for TT-exported ZN quote CSV.
Stdlib only: csv, pathlib, dataclasses.

Net P&L is accumulated line-by-line per fill:
  net_fill = gross_price_pnl - fee_usd   (fee = $0.50 per lot per side)
Round-turn on 1 lot = $1.00 total fees when both legs are charged.
"""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass, field

from pathlib import Path

from zn_competition.economics import FeeAccounting, analyze_week_plan
from zn_competition.features import MicrostructureFeatureEngine
from zn_competition.historical import DEFAULT_ZN_MIN_DATA_PATH, load_zn_min_csv
from zn_competition.microstructure import (
    Level1MarketRow,
    Quote,
    order_book_from_quote,
    parse_level1_from_mapping,
    quote_from_level1,
)
from zn_competition.risk import FillRecord, PositionLedger, RiskState
from zn_competition.specs import (
    FEE_PER_LOT_PER_SIDE_USD,
    FEE_PER_LOT_ROUND_TURN_USD,
    weekly_volume_requirement,
)
from zn_competition.strategies.base import StrategyContext
from zn_competition.strategies.engine import StrategyStack
from zn_competition.strategies.volume_aware_mm import VolumeAwareMarketMaking

REQUIRED_PRICE_COLUMNS_ANY = (
    frozenset({"bid", "ask"}),
    frozenset({"direct_bid_price", "direct_ask_price"}),
)
REQUIRED_COLUMNS = frozenset({"timestamp"})
OPTIONAL_COLUMNS = frozenset(
    {
        "direct_bid_qty",
        "direct_ask_qty",
        "bid_order_count",
        "ask_order_count",
        "direct_bid_price",
        "direct_ask_price",
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
class NetPnLPoint:
    """Chronological mark-to-market net P&L after each bar (realized + open mark)."""

    timestamp: str
    bar_index: int
    position: int
    realized_pnl_usd: float
    mark_pnl_usd: float
    cumulative_net_pnl_usd: float
    fees_paid_usd: float


@dataclass(frozen=True)
class FillPnLLine:
    """Per-fill P&L attribution (one exchange leg)."""

    index: int
    timestamp: str
    reason: str
    side: str
    lots: int
    price: float
    gross_pnl_usd: float
    fee_usd: float
    net_pnl_usd: float
    position_after: int
    cumulative_net_pnl_usd: float


@dataclass(frozen=True)
class BacktestResult:
    week: int
    leg_lots_traded: int
    weekly_min_legs: int
    met_volume_min: bool
    position_end: int
    gross_pnl_usd: float
    total_fees_usd: float
    realized_pnl_usd: float
    mark_pnl_usd: float
    net_pnl_usd: float
    fill_count: int
    halted: bool
    halt_reason: str
    signals_by_reason: dict[str, int]
    pnl_lines: tuple[FillPnLLine, ...] = field(default_factory=tuple)
    net_pnl_curve: tuple[NetPnLPoint, ...] = field(default_factory=tuple)
    actions_by_engine: dict[str, int] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2)

    def verify_fee_schedule(self) -> None:
        """Assert fees match $0.50 × leg count."""
        expected = self.leg_lots_traded * FEE_PER_LOT_PER_SIDE_USD
        if abs(self.total_fees_usd - expected) > 0.001:
            raise ValueError(
                f"fee mismatch: total_fees={self.total_fees_usd} "
                f"expected={expected}"
            )

    def verify_net_pnl_identity(self) -> None:
        """Net = realized (per-fill net sum) + open-position mark."""
        expected = self.realized_pnl_usd + self.mark_pnl_usd
        if abs(self.net_pnl_usd - expected) > 0.015:
            raise ValueError(
                f"net mismatch: net={self.net_pnl_usd} expected={expected}"
            )
        if self.pnl_lines:
            line_sum = sum(line.net_pnl_usd for line in self.pnl_lines)
            if abs(line_sum - self.realized_pnl_usd) > 0.01:
                raise ValueError(
                    f"realized mismatch: ledger={self.realized_pnl_usd} "
                    f"lines={line_sum}"
                )


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
        if "timestamp" not in headers:
            raise ValueError("CSV missing required column: timestamp")
        if not any(req <= headers for req in REQUIRED_PRICE_COLUMNS_ANY):
            raise ValueError(
                "CSV must include bid+ask or direct_bid_price+direct_ask_price"
            )
        for line_no, row in enumerate(reader, start=2):
            try:
                last_raw = row.get("last", "").strip()
                l1 = parse_level1_from_mapping(
                    row,
                    default_direct_bid_qty=10,
                    default_direct_ask_qty=10,
                )
                direct_bid, direct_ask = _parse_level1_prices(row)
                quotes.append(
                    Quote(
                        timestamp=row["timestamp"].strip(),
                        bid=direct_bid,
                        ask=direct_ask,
                        direct_bid_qty=l1.direct_bid_qty,
                        direct_ask_qty=l1.direct_ask_qty,
                        bid_order_count=l1.bid_order_count,
                        ask_order_count=l1.ask_order_count,
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


def _parse_level1_prices(row: dict[str, str]) -> tuple[float, float]:
    """Read inside direct prices from Level 1 or legacy bid/ask columns."""
    bid_raw = (
        row.get("direct_bid_price", "").strip()
        or row.get("bid", "").strip()
    )
    ask_raw = (
        row.get("direct_ask_price", "").strip()
        or row.get("ask", "").strip()
    )
    if not bid_raw or not ask_raw:
        raise ValueError("missing direct_bid_price/direct_ask_price or bid/ask")
    return float(bid_raw), float(ask_raw)


def _competition_fee_usd(lots: int) -> float:
    """Flat $0.50 per lot per side — every entry and exit leg."""
    return lots * FEE_PER_LOT_PER_SIDE_USD


def _fill_with_competition_fee(fill: FillRecord) -> FillRecord:
    """Normalize fill fee to leaderboard accounting ($0.50/lot/side)."""
    fee = _competition_fee_usd(fill.lots)
    return FillRecord(
        side=fill.side,
        lots=fill.lots,
        price=fill.price,
        fee_usd=fee,
        timestamp=fill.timestamp,
        reason=fill.reason,
    )


def _apply_fill_and_record_pnl(
    ledger: PositionLedger,
    fill: FillRecord,
    fill_index: int,
    pnl_lines: list[FillPnLLine],
) -> float:
    """Apply one leg with competition fee and append P&L line."""
    scored = _fill_with_competition_fee(fill)
    net_this_fill = ledger.apply_fill(scored)
    _record_fill_pnl(ledger, fill_index, net_this_fill, scored.fee_usd, pnl_lines)
    return net_this_fill


def _record_fill_pnl(
    ledger: PositionLedger,
    fill_index: int,
    net_this_fill: float,
    fee_usd: float,
    pnl_lines: list[FillPnLLine],
    cumulative_net_pnl_usd: float | None = None,
) -> None:
    """Append one line-level P&L row after ``ledger.apply_fill``."""
    last = ledger.fills[-1]
    gross = net_this_fill + fee_usd
    pnl_lines.append(
        FillPnLLine(
            index=fill_index,
            timestamp=last.timestamp,
            reason=last.reason,
            side=last.side.value,
            lots=last.lots,
            price=last.price,
            gross_pnl_usd=gross,
            fee_usd=fee_usd,
            net_pnl_usd=net_this_fill,
            position_after=ledger.position,
            cumulative_net_pnl_usd=(
                cumulative_net_pnl_usd
                if cumulative_net_pnl_usd is not None
                else ledger.realized_pnl_usd
            ),
        )
    )


@dataclass(frozen=True)
class _LedgerSnapshot:
    position: int
    avg_entry_price: float


def _snapshot_ledger(ledger: PositionLedger) -> _LedgerSnapshot:
    return _LedgerSnapshot(
        position=ledger.position,
        avg_entry_price=ledger.avg_entry_price,
    )


def _record_vamm_fills(
    snap: _LedgerSnapshot,
    fills: list[FillRecord],
    fill_index: int,
    pnl_lines: list[FillPnLLine],
    cumulative_realized_start: float,
) -> tuple[int, float]:
    """Replay VAMM fills on a scratch ledger for per-leg net P&L attribution."""
    scratch = PositionLedger()
    scratch.position = snap.position
    scratch.avg_entry_price = snap.avg_entry_price
    cumulative = cumulative_realized_start

    for fill in fills:
        scored = _fill_with_competition_fee(fill)
        net_this_fill = scratch.apply_fill(scored)
        cumulative += net_this_fill
        _record_fill_pnl(
            scratch,
            fill_index,
            net_this_fill,
            scored.fee_usd,
            pnl_lines,
            cumulative_net_pnl_usd=cumulative,
        )
        fill_index += 1

    return fill_index, cumulative


def run_backtest(
    quotes: list[Quote] | None = None,
    week: int = 1,
    stack: StrategyStack | None = None,
    daily_loss_limit_usd: float = 1_500.0,
    event_rows: list[dict[str, str]] | None = None,
    csv_path: Path | str | None = None,
    use_volume_aware_mm: bool = True,
) -> BacktestResult:
    if week < 1 or week > 4:
        raise ValueError(f"week must be 1–4, got {week}")
    if quotes is None:
        quotes = load_zn_min_csv(csv_path or DEFAULT_ZN_MIN_DATA_PATH)
    if not quotes:
        raise ValueError("quotes list is empty")

    weekly_min = weekly_volume_requirement(week)
    feature_engine = MicrostructureFeatureEngine()
    ledger = PositionLedger()
    risk = RiskState(daily_loss_limit_usd=daily_loss_limit_usd)
    signals_by_reason: dict[str, int] = {}
    actions_by_engine: dict[str, int] = {}
    pnl_lines: list[FillPnLLine] = []
    net_pnl_curve: list[NetPnLPoint] = []
    fill_index = 0

    if use_volume_aware_mm:
        vamm = VolumeAwareMarketMaking()
    else:
        strategy_stack = stack or StrategyStack()

    for idx, quote in enumerate(quotes):
        if risk.halted:
            break

        features = feature_engine.update(quote)
        tag, phase, surprise = (None, None, None)
        if event_rows and idx < len(event_rows):
            tag, phase, surprise = _row_event_fields(event_rows[idx])

        book = order_book_from_quote(quote)
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
            book=book,
            direct_bid_qty=quote.direct_bid_qty,
            direct_ask_qty=quote.direct_ask_qty,
            bid_order_count=quote.bid_order_count,
            ask_order_count=quote.ask_order_count,
            event_tag=tag,
            event_phase=phase,
            surprise_10y_equiv_bp=surprise,
        )

        if use_volume_aware_mm:
            snap = _snapshot_ledger(ledger)
            realized_before = ledger.realized_pnl_usd
            step = vamm.process_tick(quote, book, ledger)
            if step.fills:
                fill_index, _ = _record_vamm_fills(
                    snap,
                    step.fills,
                    fill_index,
                    pnl_lines,
                    realized_before,
                )
                risk.apply_realized(ledger.realized_pnl_usd - realized_before)
            if step.action not in ("none", "idle"):
                actions_by_engine[step.action] = (
                    actions_by_engine.get(step.action, 0) + 1
                )
                signals_by_reason[step.action] = (
                    signals_by_reason.get(step.action, 0) + 1
                )
        else:
            from zn_competition.execution import execute_signal

            signal = strategy_stack.on_tick(ctx)
            if signal is not None:
                signals_by_reason[signal.reason] = (
                    signals_by_reason.get(signal.reason, 0) + 1
                )
                fill = execute_signal(signal, quote, ledger.position)
                if fill is not None:
                    net_this_fill = _apply_fill_and_record_pnl(
                        ledger, fill, fill_index, pnl_lines
                    )
                    fill_index += 1
                    risk.apply_realized(net_this_fill)

        mark = quote.mid
        mark_pnl = ledger.mark_price_pnl_usd(mark)
        net_pnl_curve.append(
            NetPnLPoint(
                timestamp=quote.timestamp,
                bar_index=idx,
                position=ledger.position,
                realized_pnl_usd=round(ledger.realized_pnl_usd, 4),
                mark_pnl_usd=round(mark_pnl, 4),
                cumulative_net_pnl_usd=round(
                    ledger.realized_pnl_usd + mark_pnl, 4
                ),
                fees_paid_usd=round(ledger.total_fees_usd, 4),
            )
        )

    mark_pnl = ledger.mark_price_pnl_usd(quotes[-1].mid)
    gross_realized = ledger.realized_pnl_usd + ledger.total_fees_usd
    net_total = ledger.realized_pnl_usd + mark_pnl

    result = BacktestResult(
        week=week,
        leg_lots_traded=ledger.leg_lots_traded,
        weekly_min_legs=weekly_min,
        met_volume_min=ledger.leg_lots_traded >= weekly_min,
        position_end=ledger.position,
        gross_pnl_usd=round(gross_realized, 2),
        total_fees_usd=round(ledger.total_fees_usd, 2),
        realized_pnl_usd=round(ledger.realized_pnl_usd, 2),
        mark_pnl_usd=round(mark_pnl, 2),
        net_pnl_usd=round(net_total, 2),
        fill_count=len(ledger.fills),
        halted=risk.halted,
        halt_reason=risk.halt_reason,
        signals_by_reason=signals_by_reason,
        pnl_lines=tuple(pnl_lines),
        net_pnl_curve=tuple(net_pnl_curve),
        actions_by_engine=actions_by_engine,
    )
    result.verify_fee_schedule()
    result.verify_net_pnl_identity()
    return result


def run_backtest_csv(path: Path, week: int = 1) -> BacktestResult:
    quotes = load_zn_min_csv(path)
    event_rows: list[dict[str, str]] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
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
        direct_bid = mid - half_spread
        direct_ask = mid + half_spread
        row = Level1MarketRow(
            direct_bid_price=round(direct_bid, 6),
            direct_ask_price=round(direct_ask, 6),
            direct_bid_qty=20 + (i % 5),
            direct_ask_qty=18 + (i % 7),
            bid_order_count=3 + (i % 4),
            ask_order_count=2 + (i % 3),
            timestamp=f"2026-06-03T14:{i % 60:02d}:00+00:00",
            volume=1 + (i % 3),
        )
        quotes.append(quote_from_level1(row))
    return quotes


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        result = run_backtest_csv(
            Path(sys.argv[1]),
            week=int(sys.argv[2]) if len(sys.argv) > 2 else 1,
        )
    else:
        result = run_backtest(week=1)
    print(result.to_json())
    plan = analyze_week_plan(1, result.leg_lots_traded)
    print("week_plan:", plan)
    print("fee_accounting:", asdict(FeeAccounting(result.leg_lots_traded)))
    print(
        f"fee_per_rt_check: {result.leg_lots_traded} legs, "
        f"${result.total_fees_usd} fees "
        f"(expected ${result.leg_lots_traded * FEE_PER_LOT_PER_SIDE_USD}, "
        f"RT=${FEE_PER_LOT_ROUND_TURN_USD}/lot)"
    )
    if result.net_pnl_curve:
        first = result.net_pnl_curve[0]
        last = result.net_pnl_curve[-1]
        print(
            f"net_pnl_curve: {len(result.net_pnl_curve)} bars, "
            f"start=${first.cumulative_net_pnl_usd:.2f}, "
            f"end=${last.cumulative_net_pnl_usd:.2f}"
        )
