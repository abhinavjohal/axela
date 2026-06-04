"""
Historical simulation loop — mock order book feed through all strategy modules.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping

from zn_competition.execution import ExecutionEngine, ExecutionRiskException
from zn_competition.features import MicrostructureFeatureEngine
from zn_competition.microstructure import (
    Level1MarketRow,
    Quote,
    order_book_from_quote,
    quote_from_level1,
)
from zn_competition.risk import ExecutionException, PositionLedger, RiskState
from zn_competition.specs import (
    FEE_PER_LOT_PER_SIDE_USD,
    MAX_POSITION_LOTS,
    TICK_SIZE_FLOAT,
    ZN_SEP26,
    weekly_volume_requirement,
)

DEFAULT_ZN_MIN_DATA_PATH = Path(__file__).resolve().parent / "data" / "zn_min_data.csv"

_TIMESTAMP_ALIASES = (
    "timestamp",
    "Timestamp (UTC)",
    "time",
    "datetime",
    "DateTime",
    "date",
    "Date",
)
_DIRECT_BID_PRICE_ALIASES = (
    "direct_bid_price",
    "bid",
    "Bid",
    "bid_price",
    "BidPrice",
    "best_bid",
)
_DIRECT_ASK_PRICE_ALIASES = (
    "direct_ask_price",
    "ask",
    "Ask",
    "ask_price",
    "AskPrice",
    "best_ask",
)
_DIRECT_BID_QTY_ALIASES = (
    "direct_bid_qty",
    "bid_qty",
    "BidQty",
    "bid_size",
    "BidSize",
    "bid_quantity",
)
_DIRECT_ASK_QTY_ALIASES = (
    "direct_ask_qty",
    "ask_qty",
    "AskQty",
    "ask_size",
    "AskSize",
    "ask_quantity",
)
_BID_ORDER_COUNT_ALIASES = (
    "bid_order_count",
    "bid_orders",
    "BidOrders",
    "bid_count",
)
_ASK_ORDER_COUNT_ALIASES = (
    "ask_order_count",
    "ask_orders",
    "AskOrders",
    "ask_count",
)
_OHLC_CLOSE_ALIASES = ("Close", "close", "last", "Last")
_OHLC_HIGH_ALIASES = ("High", "high")
_OHLC_LOW_ALIASES = ("Low", "low")
_OHLC_OPEN_ALIASES = ("Open", "open")


def _cell(row: Mapping[str, str], *aliases: str) -> str:
    """Return first non-empty cell matching any alias (case-insensitive)."""
    normalized = {k.strip().lower(): v.strip() for k, v in row.items() if v is not None}
    for alias in aliases:
        key = alias.strip().lower()
        if key in normalized and normalized[key]:
            return normalized[key]
    return ""


def _parse_float(value: str, default: float = 0.0) -> float:
    if not value:
        return default
    return float(value.replace(",", ""))


def _parse_int(value: str, default: int = 0) -> int:
    if not value:
        return default
    return int(float(value.replace(",", "")))


def _normalize_timestamp(raw: str) -> str:
    text = raw.strip()
    if not text:
        return text
    if "T" in text:
        return text if "+" in text or text.endswith("Z") else f"{text}+00:00"
    return text.replace(" ", "T") + "+00:00"


def row_to_level1_dict(row: Mapping[str, str]) -> dict[str, float | int | str]:
    """
    Map one CSV row to internal Level 1 fields.

    Supports explicit L1 columns or OHLC-only bars (derives inside market and
    qty proxies from bar geometry for OBI simulation).
    """
    timestamp = _normalize_timestamp(_cell(row, *_TIMESTAMP_ALIASES))
    if not timestamp:
        raise ValueError("CSV row missing timestamp column")

    bid_price_raw = _cell(row, *_DIRECT_BID_PRICE_ALIASES)
    ask_price_raw = _cell(row, *_DIRECT_ASK_PRICE_ALIASES)
    bid_qty_raw = _cell(row, *_DIRECT_BID_QTY_ALIASES)
    ask_qty_raw = _cell(row, *_DIRECT_ASK_QTY_ALIASES)
    bid_count_raw = _cell(row, *_BID_ORDER_COUNT_ALIASES)
    ask_count_raw = _cell(row, *_ASK_ORDER_COUNT_ALIASES)

    close = _parse_float(_cell(row, *_OHLC_CLOSE_ALIASES))
    high = _parse_float(_cell(row, *_OHLC_HIGH_ALIASES), close)
    low = _parse_float(_cell(row, *_OHLC_LOW_ALIASES), close)
    open_ = _parse_float(_cell(row, *_OHLC_OPEN_ALIASES), close)

    if close:
        anchor = close
    elif high and low:
        anchor = (high + low) / 2.0
    else:
        anchor = open_

    bar_high = max(high, anchor, low) if anchor else high
    bar_low = min(low, anchor, high) if anchor else low

    if bid_price_raw and ask_price_raw:
        direct_bid_price = ZN_SEP26.round_price_to_tick(_parse_float(bid_price_raw))
        direct_ask_price = ZN_SEP26.round_price_to_tick(_parse_float(ask_price_raw))
    elif anchor:
        half_spread = max((bar_high - bar_low) / 2.0, TICK_SIZE_FLOAT / 2.0)
        direct_bid_price = ZN_SEP26.round_price_to_tick(anchor - half_spread)
        direct_ask_price = ZN_SEP26.round_price_to_tick(anchor + half_spread)
        if direct_ask_price <= direct_bid_price:
            direct_ask_price = ZN_SEP26.round_price_to_tick(
                direct_bid_price + TICK_SIZE_FLOAT
            )
    else:
        raise ValueError(f"Row {timestamp!r}: no bid/ask or OHLC price columns")

    if bid_qty_raw and ask_qty_raw:
        direct_bid_qty = max(1, _parse_int(bid_qty_raw, 1))
        direct_ask_qty = max(1, _parse_int(ask_qty_raw, 1))
    elif anchor:
        tick = TICK_SIZE_FLOAT
        close_to_low_ticks = max(0.0, (anchor - bar_low) / tick)
        close_to_high_ticks = max(0.0, (bar_high - anchor) / tick)
        base_qty = 20
        skew = 30
        direct_bid_qty = max(1, int(base_qty + close_to_low_ticks * skew))
        direct_ask_qty = max(1, int(base_qty + close_to_high_ticks * skew))
    else:
        direct_bid_qty = max(1, _parse_int(bid_qty_raw, 10))
        direct_ask_qty = max(1, _parse_int(ask_qty_raw, 10))

    bid_order_count = max(1, _parse_int(bid_count_raw, max(1, direct_bid_qty // 10)))
    ask_order_count = max(1, _parse_int(ask_count_raw, max(1, direct_ask_qty // 10)))

    return {
        "timestamp": timestamp,
        "direct_bid_price": direct_bid_price,
        "direct_ask_price": direct_ask_price,
        "direct_bid_qty": direct_bid_qty,
        "direct_ask_qty": direct_ask_qty,
        "bid_order_count": bid_order_count,
        "ask_order_count": ask_order_count,
    }


def level1_dict_to_quote(fields: Mapping[str, float | int | str]) -> Quote:
    return quote_from_level1(
        Level1MarketRow(
            timestamp=str(fields["timestamp"]),
            direct_bid_price=float(fields["direct_bid_price"]),
            direct_ask_price=float(fields["direct_ask_price"]),
            direct_bid_qty=int(fields["direct_bid_qty"]),
            direct_ask_qty=int(fields["direct_ask_qty"]),
            bid_order_count=int(fields["bid_order_count"]),
            ask_order_count=int(fields["ask_order_count"]),
        )
    )


def load_zn_min_csv(path: Path | str | None = None) -> list[Quote]:
    """
    Load 1-minute ZN historical CSV chronologically into Level 1 ``Quote`` rows.

    Default path: ``zn_competition/data/zn_min_data.csv``.
    """
    csv_path = Path(path) if path is not None else DEFAULT_ZN_MIN_DATA_PATH
    if not csv_path.is_file():
        raise FileNotFoundError(f"Historical CSV not found: {csv_path}")

    rows: list[tuple[str, Quote]] = []
    with csv_path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError(f"CSV has no header row: {csv_path}")
        for line_no, row in enumerate(reader, start=2):
            if not any(v and str(v).strip() for v in row.values()):
                continue
            try:
                fields = row_to_level1_dict(row)
                quote = level1_dict_to_quote(fields)
                rows.append((str(fields["timestamp"]), quote))
            except (ValueError, TypeError) as exc:
                raise ValueError(f"{csv_path}:{line_no}: {exc}") from exc

    if not rows:
        raise ValueError(f"no quotes loaded from {csv_path}")

    rows.sort(key=lambda item: item[0])
    return [quote for _, quote in rows]
from zn_competition.strategies.base import StrategyContext
from zn_competition.strategies.engine import StrategyStack
from zn_competition.strategies.macro_event import MacroEventStrategy
from zn_competition.strategies.session_mr import SessionMeanReversionStrategy
from zn_competition.strategies.volume_aware_mm import OrderBookImbalanceHFT


@dataclass(frozen=True)
class HistoricalSummary:
    total_lots_traded: int
    gross_pnl_usd: float
    total_transaction_fees_usd: float
    net_pnl_usd: float
    position_end: int
    fill_count: int
    ticks_processed: int
    signals_by_reason: dict[str, int] = field(default_factory=dict)
    actions_by_engine: dict[str, int] = field(default_factory=dict)

    def format_report(self) -> str:
        lines = [
            "=" * 52,
            "  ZN Sep26 — Historical Simulation Summary",
            "=" * 52,
            f"  Total Lots Traded:        {self.total_lots_traded}",
            f"  Gross P&L:                ${self.gross_pnl_usd:,.2f}",
            f"  Total Transaction Fees:   ${self.total_transaction_fees_usd:,.2f}"
            f"  ($0.50 × {self.total_lots_traded} legs)",
            f"  Net P&L:                  ${self.net_pnl_usd:,.2f}",
            "-" * 52,
            f"  Fills: {self.fill_count}  |  Ticks: {self.ticks_processed}"
            f"  |  Position end: {self.position_end}",
            "=" * 52,
        ]
        if self.signals_by_reason:
            lines.insert(-2, "  Signals: " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.signals_by_reason.items())
            ))
        if self.actions_by_engine:
            lines.insert(-2, "  Engines: " + ", ".join(
                f"{k}={v}" for k, v in sorted(self.actions_by_engine.items())
            ))
        return "\n".join(lines)


def generate_mock_order_book_stream(
    count: int = 400,
    base_price: float = 112.0,
) -> list[Quote]:
    """
    Deterministic mock Level 1 book updates for offline strategy replay.

    Alternates bid-heavy / ask-heavy windows to exercise OBI and churn paths.
    """
    if count < 20:
        raise ValueError("count must be >= 20")
    quotes: list[Quote] = []
    mid = base_price
    half_spread = 1 / 128

    for i in range(count):
        drift = ((i % 13) - 6) * (TICK_SIZE_FLOAT * 0.08)
        mid = max(100.0, mid + drift)
        bid = round(mid - half_spread, 6)
        ask = round(mid + half_spread, 6)

        if i % 40 < 20:
            direct_bid_qty, direct_ask_qty = 125, 14
            bid_order_count, ask_order_count = 18, 4
        elif i % 40 < 30:
            direct_bid_qty, direct_ask_qty = 14, 125
            bid_order_count, ask_order_count = 4, 18
        else:
            direct_bid_qty, direct_ask_qty = 40, 40
            bid_order_count, ask_order_count = 8, 8

        quotes.append(
            Quote(
                timestamp=f"2026-06-03T14:{i % 60:02d}:{i % 60:02d}+00:00",
                bid=bid,
                ask=ask,
                direct_bid_qty=direct_bid_qty,
                direct_ask_qty=direct_ask_qty,
                bid_order_count=bid_order_count,
                ask_order_count=ask_order_count,
                volume=1 + (i % 4),
            )
        )
    return quotes


def _build_context(
    quote: Quote,
    features,
    ledger: PositionLedger,
    week: int,
    weekly_min: int,
    tag: str | None = None,
    phase: str | None = None,
    surprise: float | None = None,
) -> StrategyContext:
    book = order_book_from_quote(quote)
    return StrategyContext(
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


def _assert_ledger_position_cap(ledger: PositionLedger) -> None:
    if abs(ledger.position) > MAX_POSITION_LOTS:
        raise ExecutionException(
            f"POSITION_CAP: ledger position {ledger.position} exceeds "
            f"maximum {MAX_POSITION_LOTS} lots"
        )


class HistoricalSimulator:
    """
    Replays mock book updates through macro, session MR, OBI HFT, and volume churner.
    """

    def __init__(self, week: int = 1, daily_loss_limit_usd: float = 1_500.0) -> None:
        if week < 1 or week > 4:
            raise ValueError(f"week must be 1–4, got {week}")
        self.week = week
        self.weekly_min = weekly_volume_requirement(week)
        self.ledger = PositionLedger()
        self.risk = RiskState(daily_loss_limit_usd=daily_loss_limit_usd)
        self.feature_engine = MicrostructureFeatureEngine()
        self.session_mr = SessionMeanReversionStrategy(enable_volume_churn=True)
        self.execution = ExecutionEngine()
        self.stack = StrategyStack(
            strategies=[
                MacroEventStrategy(),
                self.session_mr,
            ],
            execution=self.execution,
        )
        self.obi = OrderBookImbalanceHFT()
        self.signals_by_reason: dict[str, int] = {}
        self.actions_by_engine: dict[str, int] = {}

    def _record_action(self, engine: str) -> None:
        self.actions_by_engine[engine] = self.actions_by_engine.get(engine, 0) + 1

    def _process_stack_tick(self, ctx, quote: Quote) -> None:
        pnl_before = self.ledger.realized_pnl_usd
        result = self.stack.process_tick(ctx, quote, self.ledger)
        if result.purge_report and result.purge_report.canceled_count > 0:
            self.actions_by_engine["regime_purge"] = (
                self.actions_by_engine.get("regime_purge", 0)
                + result.purge_report.canceled_count
            )
        if result.signal is None:
            return
        self.signals_by_reason[result.signal.reason] = (
            self.signals_by_reason.get(result.signal.reason, 0) + 1
        )
        self.risk.apply_realized(self.ledger.realized_pnl_usd - pnl_before)
        self._record_action("strategy_stack")

    def on_book_update(self, quote: Quote) -> None:
        if self.risk.halted:
            return

        _assert_ledger_position_cap(self.ledger)
        features = self.feature_engine.update(quote)
        book = order_book_from_quote(quote)
        ctx = _build_context(quote, features, self.ledger, self.week, self.weekly_min)

        try:
            self._process_stack_tick(ctx, quote)
        except ExecutionRiskException:
            self.actions_by_engine["execution_blocked"] = (
                self.actions_by_engine.get("execution_blocked", 0) + 1
            )

        _assert_ledger_position_cap(self.ledger)
        ctx = _build_context(quote, features, self.ledger, self.week, self.weekly_min)

        obi_result = self.obi.process_tick(quote, book, self.ledger)
        if obi_result.action != "none" and obi_result.action != "idle":
            self._record_action(f"obi:{obi_result.action}")

        _assert_ledger_position_cap(self.ledger)
        ctx = _build_context(quote, features, self.ledger, self.week, self.weekly_min)

        churn = self.session_mr.process_churn_pulse(
            quote, book, self.ledger, ctx
        )
        if churn is not None and churn.action not in ("idle", "none", "pulse_wait"):
            self._record_action(f"churn:{churn.action}")

        _assert_ledger_position_cap(self.ledger)

    def run(self, quotes: list[Quote]) -> HistoricalSummary:
        if not quotes:
            raise ValueError("quotes list is empty")

        for quote in quotes:
            self.on_book_update(quote)

        mark = quotes[-1].mid
        mark_pnl = self.ledger.mark_price_pnl_usd(mark)
        fees = self.ledger.total_fees_usd
        expected_fees = self.ledger.leg_lots_traded * FEE_PER_LOT_PER_SIDE_USD
        if abs(fees - expected_fees) > 0.001:
            raise RuntimeError(
                f"fee mismatch: ledger {fees} vs 0.50×legs {expected_fees}"
            )

        gross = self.ledger.realized_pnl_usd + fees + mark_pnl
        net = self.ledger.realized_pnl_usd + mark_pnl

        return HistoricalSummary(
            total_lots_traded=self.ledger.leg_lots_traded,
            gross_pnl_usd=round(gross, 2),
            total_transaction_fees_usd=round(fees, 2),
            net_pnl_usd=round(net, 2),
            position_end=self.ledger.position,
            fill_count=len(self.ledger.fills),
            ticks_processed=len(quotes),
            signals_by_reason=dict(self.signals_by_reason),
            actions_by_engine=dict(self.actions_by_engine),
        )


def run_historical_loop(
    quotes: list[Quote] | None = None,
    week: int = 1,
    csv_path: Path | str | None = None,
) -> HistoricalSummary:
    if quotes is not None:
        stream = quotes
    elif csv_path is not None or DEFAULT_ZN_MIN_DATA_PATH.is_file():
        stream = load_zn_min_csv(csv_path)
    else:
        stream = generate_mock_order_book_stream()
    return HistoricalSimulator(week=week).run(stream)


def print_historical_summary(summary: HistoricalSummary) -> None:
    print(summary.format_report())
