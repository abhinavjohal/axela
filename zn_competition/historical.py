"""
Historical simulation loop — mock order book feed through all strategy modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zn_competition.execution import ExecutionEngine, ExecutionRiskException
from zn_competition.features import MicrostructureFeatureEngine
from zn_competition.microstructure import Quote, order_book_from_quote
from zn_competition.risk import ExecutionException, PositionLedger, RiskState
from zn_competition.specs import (
    FEE_PER_LOT_PER_SIDE_USD,
    MAX_POSITION_LOTS,
    TICK_SIZE_FLOAT,
    weekly_volume_requirement,
)
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
) -> HistoricalSummary:
    stream = quotes if quotes is not None else generate_mock_order_book_stream()
    return HistoricalSimulator(week=week).run(stream)


def print_historical_summary(summary: HistoricalSummary) -> None:
    print(summary.format_report())
