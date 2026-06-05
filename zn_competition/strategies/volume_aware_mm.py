"""
High-frequency Order Book Imbalance (OBI) strategy — Level 1 direct qty.

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
    InstrumentSpec,
    MAX_POSITION_LOTS,
    get_instrument_spec,
)
from zn_competition.strategies.base import Side, Signal, StrategyContext
from zn_competition.strategies.obi_regime import (
    OBIRegimeMode,
    OBIRegimeSnapshot,
    SNIPER_THRESHOLD,
    VOLUME_THRESHOLD,
)

# Alpha sniper default (24/7) — do not confuse with deprecated volume-mode OBI 0.65
SNIPER_OBI_THRESHOLD = SNIPER_THRESHOLD  # 0.85

# Legacy default for unit tests and static HFT instances
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

    def configure_thresholds(self, entry_threshold: float) -> None:
        """Set symmetric long/short entry and flip thresholds for this bar."""
        self.entry_threshold = entry_threshold
        self.flip_threshold = -entry_threshold
        self.short_entry_threshold = -entry_threshold

    def cancel_stale_resting_orders(self) -> bool:
        """Drop unfilled working limits (e.g. on dual-regime clock shift)."""
        had_order = self._working_order is not None
        self._working_order = None
        return had_order

    def blocks_volume_churn(self) -> bool:
        """True when OBI has inventory or a resting entry (churn must wait)."""
        return self._open_trade is not None or self._working_order is not None

    @property
    def has_open_trade(self) -> bool:
        return self._open_trade is not None

    def book_from_context(self, ctx: StrategyContext) -> OrderBookSnapshot:
        if ctx.book is not None:
            return ctx.book
        return OrderBookSnapshot(
            timestamp="",
            bid=ctx.bid,
            ask=ctx.ask,
            direct_bid_qty=ctx.direct_bid_qty,
            direct_ask_qty=ctx.direct_ask_qty,
            bid_order_count=ctx.bid_order_count,
            ask_order_count=ctx.ask_order_count,
        )

    def _instrument_spec(self, book: OrderBookSnapshot) -> InstrumentSpec:
        return get_instrument_spec(book.instrument_id)

    def _liquidity_sufficient(self, book: OrderBookSnapshot) -> bool:
        spec = self._instrument_spec(book)
        return spec.liquidity_sufficient(book.direct_bid_qty, book.direct_ask_qty)

    def _favorable_ticks(self, book: OrderBookSnapshot, trade: OpenOBITrade) -> float:
        spec = self._instrument_spec(book)
        if trade.side == Side.BUY:
            return spec.price_delta_to_ticks(book.inside_bid - trade.entry_price)
        return spec.price_delta_to_ticks(trade.entry_price - book.inside_ask)

    def _exit_price_for_trade(
        self,
        book: OrderBookSnapshot,
        trade: OpenOBITrade,
    ) -> float:
        spec = self._instrument_spec(book)
        profit_ticks = spec.alpha_profit_buffer_ticks()
        if profit_ticks <= 0:
            return spec.round_price_to_tick(trade.entry_price)
        if trade.side == Side.BUY:
            return spec.round_price_to_tick(
                trade.entry_price + spec.ticks_to_price_delta(profit_ticks)
            )
        return spec.round_price_to_tick(
            trade.entry_price - spec.ticks_to_price_delta(profit_ticks)
        )

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        """Signal-only path for strategy stack (no ledger mutation)."""
        if not self._session_ok(ctx):
            return None
        book = self.book_from_context(ctx)
        obi = calculate_order_book_imbalance(book)
        position = ctx.position

        if self._open_trade is not None:
            if self._should_take_profit(book, self._open_trade):
                spec = self._instrument_spec(book)
                return Signal(
                    side=Side.FLAT,
                    size=self._open_trade.lots,
                    urgency="aggressive",
                    reason="obi_take_profit",
                    expected_edge_ticks=float(spec.alpha_profit_buffer_ticks()),
                    max_hold_seconds=1,
                )
            if self._should_scratch(obi, self._open_trade, book):
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

        if not self._liquidity_sufficient(book):
            return None

        if obi >= self.entry_threshold and position < MAX_POSITION_LOTS:
            lots = clip_order_size(self.quote_size, position)
            if lots <= 0:
                return None
            return Signal(
                side=Side.BUY,
                size=lots,
                urgency="passive",
                reason="obi_passive_bid",
                expected_edge_ticks=self.entry_threshold,
                max_hold_seconds=30,
            )

        if obi <= self.short_entry_threshold and position > -MAX_POSITION_LOTS:
            lots = clip_order_size(self.quote_size, position)
            if lots <= 0:
                return None
            return Signal(
                side=Side.SELL,
                size=lots,
                urgency="passive",
                reason="obi_passive_ask",
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
            if self._should_take_profit(book, self._open_trade):
                exit_fills, pnl = self._execute_profit_exit(quote, book, ledger)
                result.fills.extend(exit_fills)
                result.scratch_pnl_usd = pnl
                result.action = "take_profit"
                return result
            if self._should_scratch(obi, self._open_trade, book):
                exit_fills, pnl = self._execute_scratch(quote, book, ledger)
                result.fills.extend(exit_fills)
                result.scratch_pnl_usd = pnl
                result.action = "scratch"
                return result

        if self._working_order is not None:
            if not self._liquidity_sufficient(book):
                self._working_order = None
                result.action = "liquidity_dropout"
                return result
            fill = self._try_fill_working_order(quote, book, ledger, obi)
            if fill is not None:
                result.fills.append(fill)
                result.action = "fill_passive"
                return result
            working = self._working_order
            if working is not None and not self._still_quoting_favorable(
                obi, working.side
            ):
                self._working_order = None
                result.action = "cancel_unfavorable"

        if self._open_trade is not None or self._working_order is not None:
            return result

        if not self._allows_new_entries():
            return result

        if not self._liquidity_sufficient(book):
            result.action = "liquidity_dropout"
            return result

        if obi >= self.entry_threshold and position < MAX_POSITION_LOTS:
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

        if obi <= self.short_entry_threshold and position > -MAX_POSITION_LOTS:
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

    def _allows_new_entries(self) -> bool:
        return True

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

    def _should_take_profit(self, book: OrderBookSnapshot, trade: OpenOBITrade) -> bool:
        spec = self._instrument_spec(book)
        profit_ticks = spec.alpha_profit_buffer_ticks()
        if profit_ticks <= 0:
            return False
        return self._favorable_ticks(book, trade) >= profit_ticks

    def _should_scratch(
        self,
        obi: float,
        trade: OpenOBITrade,
        book: OrderBookSnapshot,
    ) -> bool:
        flipped = False
        if trade.side == Side.BUY and obi <= self.flip_threshold:
            flipped = True
        elif trade.side == Side.SELL and obi >= -self.flip_threshold:
            flipped = True
        if not flipped:
            return False
        spec = self._instrument_spec(book)
        profit_ticks = spec.alpha_profit_buffer_ticks()
        if profit_ticks <= 0:
            return True
        return self._favorable_ticks(book, trade) >= profit_ticks

    def _still_quoting_favorable(self, obi: float, side: Side) -> bool:
        if side == Side.BUY:
            return obi >= self.entry_threshold * 0.5
        if side == Side.SELL:
            return obi <= self.short_entry_threshold * 0.5
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
        order = OrderRequest(Side.BUY, lots, reason="obi_passive_bid")
        validate_order(ledger.position, order)
        return WorkingLimitOrder(
            side=Side.BUY,
            lots=lots,
            limit_price=limit_price,
            placed_at=quote.timestamp,
            reason="obi_passive_bid",
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
        order = OrderRequest(Side.SELL, lots, reason="obi_passive_ask")
        validate_order(ledger.position, order)
        return WorkingLimitOrder(
            side=Side.SELL,
            lots=lots,
            limit_price=limit_price,
            placed_at=quote.timestamp,
            reason="obi_passive_ask",
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

        spec = self._instrument_spec(book)
        if order.side == Side.BUY:
            if abs(spec.round_price_to_tick(quote.bid) - order.limit_price) > 1e-9:
                return None
        elif order.side == Side.SELL:
            if abs(spec.round_price_to_tick(quote.ask) - order.limit_price) > 1e-9:
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

    def _execute_profit_exit(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
    ) -> tuple[list[FillRecord], float]:
        return self._execute_open_trade_exit(
            quote,
            book,
            ledger,
            reason="obi_take_profit",
        )

    def _execute_scratch(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
    ) -> tuple[list[FillRecord], float]:
        return self._execute_open_trade_exit(
            quote,
            book,
            ledger,
            reason="obi_scratch_flip",
        )

    def _execute_open_trade_exit(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        *,
        reason: str,
    ) -> tuple[list[FillRecord], float]:
        trade = self._open_trade
        if trade is None:
            return [], 0.0

        spec = self._instrument_spec(book)
        exit_side = Side.SELL if trade.side == Side.BUY else Side.BUY
        exit_price = self._exit_price_for_trade(book, trade)
        profit_ticks = spec.alpha_profit_buffer_ticks()
        if profit_ticks > 0:
            tick_move = float(profit_ticks)
        else:
            tick_move = 0.0

        validate_order(
            ledger.position,
            OrderRequest(exit_side, trade.lots, reason=reason),
        )

        exit_fill = FillRecord(
            side=exit_side,
            lots=trade.lots,
            price=exit_price,
            fee_usd=trade.lots * FEE_PER_LOT_PER_SIDE_USD,
            timestamp=quote.timestamp,
            reason=reason,
        )
        ledger.apply_fill(exit_fill)
        self._open_trade = None
        self._working_order = None
        pnl = net_pnl_from_tick_move(
            tick_move,
            trade.lots,
            sides=2,
            instrument_id=spec.instrument_id,
        ).net_pnl_usd
        if profit_ticks <= 0:
            pnl = scratch_net_pnl_usd(trade.lots)
        return [exit_fill], pnl

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


class SniperOBIEngine(OrderBookImbalanceHFT):
    """
    Alpha engine — high-selectivity OBI sniper, 24/7.

    Fixed threshold (default 0.85). Never lowered to force competition volume;
    volume is supplied by Module 4 ``VolumeChurner`` on a separate path.
    """

    name = "sniper_obi_alpha"

    def __init__(
        self,
        quote_size: int = 1,
        entry_threshold: float = SNIPER_OBI_THRESHOLD,
    ) -> None:
        super().__init__(
            quote_size=quote_size,
            entry_threshold=entry_threshold,
            flip_threshold=-entry_threshold,
            short_entry_threshold=-entry_threshold,
        )

    def _allows_new_entries(self) -> bool:
        return True


class DualRegimeOBIEngine(OrderBookImbalanceHFT):
    """
    OBI HFT with intraday dual-regime thresholds from ``DualRegimeSessionClock``.

    SNIPER_MODE @ 0.85 (08:30–11:30 ET) and VOLUME_MODE @ 0.65 (12:00–14:00 ET).
    Thresholds are applied per bar via ``apply_regime``; resting orders from the
    prior regime are canceled on clock-driven shifts.
    """

    name = "dual_regime_obi"

    def __init__(self, quote_size: int = 1) -> None:
        super().__init__(
            quote_size=quote_size,
            entry_threshold=VOLUME_THRESHOLD,
            flip_threshold=-VOLUME_THRESHOLD,
            short_entry_threshold=-VOLUME_THRESHOLD,
        )
        self._regime_mode: OBIRegimeMode = OBIRegimeMode.OFF
        self._working_order_regime: OBIRegimeMode | None = None

    @property
    def regime_mode(self) -> OBIRegimeMode:
        return self._regime_mode

    def apply_regime(self, snapshot: OBIRegimeSnapshot) -> None:
        self._regime_mode = snapshot.mode
        if snapshot.allows_new_entries:
            self.configure_thresholds(snapshot.entry_threshold)

    def _allows_new_entries(self) -> bool:
        return self._regime_mode != OBIRegimeMode.OFF

    def process_tick(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
    ) -> ExecutionStepResult:
        result = super().process_tick(quote, book, ledger)
        if result.action in ("working_passive_bid", "working_passive_ask"):
            self._working_order_regime = self._regime_mode
        return result

    def cancel_stale_resting_orders(self) -> bool:
        had = super().cancel_stale_resting_orders()
        self._working_order_regime = None
        return had

    def reset(self) -> None:
        super().reset()
        self._regime_mode = OBIRegimeMode.OFF
        self._working_order_regime = None


class VolumeAwareMarketMaking(DualRegimeOBIEngine):
    """Stack registration alias — dual-regime OBI with weekly volume gate."""

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
        if not self._allows_new_entries():
            return None
        return OrderBookImbalanceHFT.on_tick(self, ctx)
