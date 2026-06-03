"""
High-frequency Order Book Imbalance (OBI) strategy — L1 + L2 depth.

TT checklist: passive limits at inside market; $0.50/side ($1.00 RT); max 10 lots.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zn_competition.economics import net_pnl_from_tick_move
from zn_competition.microstructure import (
    OrderBookSnapshot,
    Quote,
    calculate_order_book_imbalance,
    order_book_from_quote,
)
from zn_competition.risk import (
    FillRecord,
    OrderRequest,
    PositionLedger,
    clip_order_size,
    validate_order,
)
from zn_competition.specs import (
    FEE_PER_LOT_PER_SIDE_USD,
    FEE_PER_LOT_ROUND_TURN_USD,
    HIGH_IMPACT_MACRO_TAGS,
    MAX_POSITION_LOTS,
    ZN_SEP26,
)
from zn_competition.strategies.base import Side, Signal, StrategyContext

# OBI thresholds (HFT)
OBI_ENTRY_THRESHOLD = 0.7
OBI_FLIP_AGAINST_THRESHOLD = -0.7
OBI_SHORT_ENTRY_THRESHOLD = -0.7

MAX_VOL_TICKS_1H = 4.0
MAX_SPREAD_TICKS = 2.0


@dataclass(frozen=True)
class WorkingLimitOrder:
    side: Side
    lots: int
    limit_price: float
    placed_at: str
    reason: str


@dataclass
class OpenOBITrade:
    side: Side
    lots: int
    entry_price: float
    entry_obi: float
    opened_at: str


@dataclass
class ExecutionStepResult:
    fills: list[FillRecord] = field(default_factory=list)
    obi: float = 0.0
    scratch_pnl_usd: float = 0.0
    action: str = "none"


def scratch_net_pnl_usd(lots: int) -> float:
    """Flat scratch at entry price: 0 tick gross, $1.00/lot round-turn fees."""
    return net_pnl_from_tick_move(0.0, lots, sides=2).net_pnl_usd


@dataclass
class OrderBookImbalanceHFT:
    """
    Passive inside-market OBI engine with immediate scratch on book flip.

    Entry (long):  OBI > 0.7 and position < 10  → passive limit bid @ inside bid
    Entry (short): OBI < -0.7 and position > -10 → passive limit ask @ inside ask
    Scratch:       book flips against position → exit @ entry price (−$1.00/lot RT)
    """

    name: str = "order_book_imbalance_hft"
    quote_size: int = 1
    entry_threshold: float = OBI_ENTRY_THRESHOLD
    flip_threshold: float = OBI_FLIP_AGAINST_THRESHOLD
    short_entry_threshold: float = OBI_SHORT_ENTRY_THRESHOLD

    _working_order: WorkingLimitOrder | None = field(default=None, init=False, repr=False)
    _open_trade: OpenOBITrade | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.quote_size < 1:
            raise ValueError("quote_size must be >= 1")
        if not (0.0 < self.entry_threshold <= 1.0):
            raise ValueError("entry_threshold must be in (0, 1]")
        if not (-1.0 <= self.flip_threshold < 0.0):
            raise ValueError("flip_threshold must be in [-1, 0)")

    def reset(self) -> None:
        self._working_order = None
        self._open_trade = None

    def book_from_context(self, ctx: StrategyContext) -> OrderBookSnapshot:
        if ctx.book is not None:
            return ctx.book
        return OrderBookSnapshot(
            timestamp="",
            bid=ctx.bid,
            ask=ctx.ask,
            bid_l1_size=ctx.bid_l1_size,
            ask_l1_size=ctx.ask_l1_size,
            bid_l2_size=ctx.bid_l2_size,
            ask_l2_size=ctx.ask_l2_size,
        )

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        """Signal-only path for strategy stack (no ledger mutation)."""
        if not self._session_ok(ctx):
            return None
        book = self.book_from_context(ctx)
        obi = calculate_order_book_imbalance(book)
        position = ctx.position

        if self._open_trade is not None:
            if self._should_scratch(obi, self._open_trade):
                return Signal(
                    side=Side.FLAT,
                    size=self._open_trade.lots,
                    urgency="aggressive",
                    reason="obi_scratch_flip",
                    expected_edge_ticks=0.0,
                    max_hold_seconds=1,
                )
            return None

        if self._working_order is not None:
            return None

        if obi > self.entry_threshold and position < MAX_POSITION_LOTS:
            lots = clip_order_size(self.quote_size, position)
            if lots <= 0:
                return None
            return Signal(
                side=Side.BUY,
                size=lots,
                urgency="passive",
                reason="obi_passive_bid_l2",
                expected_edge_ticks=self.entry_threshold,
                max_hold_seconds=30,
            )

        if obi < self.short_entry_threshold and position > -MAX_POSITION_LOTS:
            lots = clip_order_size(self.quote_size, position)
            if lots <= 0:
                return None
            return Signal(
                side=Side.SELL,
                size=lots,
                urgency="passive",
                reason="obi_passive_ask_l2",
                expected_edge_ticks=abs(self.short_entry_threshold),
                max_hold_seconds=30,
            )

        return None

    def process_tick(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
    ) -> ExecutionStepResult:
        """
        Full execution step: scratch → fill working order → place new passive order.

        Reads ``ledger.position`` and enforces ±10 lot cap via risk.py helpers.
        """
        if abs(ledger.position) > MAX_POSITION_LOTS:
            raise ValueError(
                f"ledger position {ledger.position} exceeds cap {MAX_POSITION_LOTS}"
            )

        obi = calculate_order_book_imbalance(book)
        result = ExecutionStepResult(obi=obi)
        position = ledger.position

        if self._open_trade is not None:
            if self._should_scratch(obi, self._open_trade):
                scratch_fills, pnl = self._execute_scratch(quote, book, ledger)
                result.fills.extend(scratch_fills)
                result.scratch_pnl_usd = pnl
                result.action = "scratch"
                return result

        if self._working_order is not None:
            fill = self._try_fill_working_order(quote, book, ledger, obi)
            if fill is not None:
                result.fills.append(fill)
                result.action = "fill_passive"
                return result
            if not self._still_quoting_favorable(obi, self._working_order.side):
                self._working_order = None
                result.action = "cancel_unfavorable"

        if self._open_trade is not None or self._working_order is not None:
            return result

        if obi > self.entry_threshold and position < MAX_POSITION_LOTS:
            order = self._place_passive_bid(quote, book, ledger, obi)
            if order is not None:
                self._working_order = order
                fill = self._try_fill_working_order(quote, book, ledger, obi)
                if fill is not None:
                    result.fills.append(fill)
                    result.action = "enter_passive_bid"
                else:
                    result.action = "working_passive_bid"
            return result

        if obi < self.short_entry_threshold and position > -MAX_POSITION_LOTS:
            order = self._place_passive_ask(quote, book, ledger, obi)
            if order is not None:
                self._working_order = order
                fill = self._try_fill_working_order(quote, book, ledger, obi)
                if fill is not None:
                    result.fills.append(fill)
                    result.action = "enter_passive_ask"
                else:
                    result.action = "working_passive_ask"
            return result

        return result

    def _session_ok(self, ctx: StrategyContext) -> bool:
        if ctx.event_tag in HIGH_IMPACT_MACRO_TAGS:
            return False
        if ctx.features is None:
            return False
        if ctx.features.realized_vol_ticks_1h > MAX_VOL_TICKS_1H:
            return False
        if ctx.features.spread_ticks > MAX_SPREAD_TICKS:
            return False
        return True

    def _should_scratch(self, obi: float, trade: OpenOBITrade) -> bool:
        if trade.side == Side.BUY and obi <= self.flip_threshold:
            return True
        if trade.side == Side.SELL and obi >= -self.flip_threshold:
            return True
        return False

    def _still_quoting_favorable(self, obi: float, side: Side) -> bool:
        if side == Side.BUY:
            return obi > self.entry_threshold * 0.5
        if side == Side.SELL:
            return obi < self.short_entry_threshold * 0.5
        return False

    def _place_passive_bid(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        obi: float,
    ) -> WorkingLimitOrder | None:
        lots = clip_order_size(self.quote_size, ledger.position)
        if lots <= 0:
            return None
        limit_price = book.inside_bid
        order = OrderRequest(Side.BUY, lots, reason="obi_passive_bid_l2")
        validate_order(ledger.position, order)
        return WorkingLimitOrder(
            side=Side.BUY,
            lots=lots,
            limit_price=limit_price,
            placed_at=quote.timestamp,
            reason="obi_passive_bid_l2",
        )

    def _place_passive_ask(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        obi: float,
    ) -> WorkingLimitOrder | None:
        lots = clip_order_size(self.quote_size, ledger.position)
        if lots <= 0:
            return None
        limit_price = book.inside_ask
        order = OrderRequest(Side.SELL, lots, reason="obi_passive_ask_l2")
        validate_order(ledger.position, order)
        return WorkingLimitOrder(
            side=Side.SELL,
            lots=lots,
            limit_price=limit_price,
            placed_at=quote.timestamp,
            reason="obi_passive_ask_l2",
        )

    def _try_fill_working_order(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        obi: float,
    ) -> FillRecord | None:
        order = self._working_order
        if order is None:
            return None
        if not self._still_quoting_favorable(obi, order.side):
            self._working_order = None
            return None

        if order.side == Side.BUY:
            if abs(ZN_SEP26.round_price_to_tick(quote.bid) - order.limit_price) > 1e-9:
                return None
            if obi < self.entry_threshold:
                return None
        elif order.side == Side.SELL:
            if abs(ZN_SEP26.round_price_to_tick(quote.ask) - order.limit_price) > 1e-9:
                return None
            if obi > self.short_entry_threshold:
                return None
        else:
            return None

        fill = FillRecord(
            side=order.side,
            lots=order.lots,
            price=order.limit_price,
            fee_usd=order.lots * FEE_PER_LOT_PER_SIDE_USD,
            timestamp=quote.timestamp,
            reason=order.reason,
        )
        validate_order(
            ledger.position,
            OrderRequest(order.side, order.lots, reason=order.reason),
        )
        ledger.apply_fill(fill)
        self._open_trade = OpenOBITrade(
            side=order.side,
            lots=order.lots,
            entry_price=order.limit_price,
            entry_obi=obi,
            opened_at=quote.timestamp,
        )
        self._working_order = None
        return fill

    def _execute_scratch(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
    ) -> tuple[list[FillRecord], float]:
        trade = self._open_trade
        if trade is None:
            return [], 0.0

        exit_side = Side.SELL if trade.side == Side.BUY else Side.BUY
        exit_price = ZN_SEP26.round_price_to_tick(trade.entry_price)

        if exit_side == Side.SELL:
            validate_order(
                ledger.position,
                OrderRequest(Side.SELL, trade.lots, reason="obi_scratch_flip"),
            )
        else:
            validate_order(
                ledger.position,
                OrderRequest(Side.BUY, trade.lots, reason="obi_scratch_flip"),
            )

        exit_fill = FillRecord(
            side=exit_side,
            lots=trade.lots,
            price=exit_price,
            fee_usd=trade.lots * FEE_PER_LOT_PER_SIDE_USD,
            timestamp=quote.timestamp,
            reason="obi_scratch_flip",
        )
        ledger.apply_fill(exit_fill)
        self._open_trade = None
        self._working_order = None
        return [exit_fill], scratch_net_pnl_usd(trade.lots)

    def sync_open_trade_from_ledger(self, ledger: PositionLedger) -> None:
        """Reconcile internal state when ledger already holds OBI inventory."""
        if ledger.position == 0:
            self._open_trade = None
            return
        if self._open_trade is not None:
            return
        side = Side.BUY if ledger.position > 0 else Side.SELL
        self._open_trade = OpenOBITrade(
            side=side,
            lots=abs(ledger.position),
            entry_price=ledger.avg_entry_price,
            entry_obi=0.0,
            opened_at="",
        )


class VolumeAwareMarketMaking(OrderBookImbalanceHFT):
    """Alias for strategy stack registration (volume-completion / OBI HFT)."""

    name = "volume_aware_mm"

    def __init__(
        self,
        quote_size: int = 1,
        weekly_min_urgency_threshold: int = 40,
    ) -> None:
        super().__init__(quote_size=quote_size)
        self.weekly_min_urgency_threshold = weekly_min_urgency_threshold

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        if ctx.weekly_min_remaining > self.weekly_min_urgency_threshold:
            return None
        return super().on_tick(ctx)
