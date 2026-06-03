"""
Session VWAP mean reversion + secondary Volume Churner (flat, two-sided inside quotes).

Volume targets (competition legs): Week1 200, W2 300, W3 400, W4 500 (2,000 total).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zn_competition.economics import net_pnl_from_tick_move
from zn_competition.microstructure import OrderBookSnapshot, Quote
from zn_competition.risk import (
    FillRecord,
    OrderRequest,
    PositionLedger,
    clip_order_size,
    validate_order,
)
from zn_competition.specs import (
    FEE_PER_LOT_PER_SIDE_USD,
    HIGH_IMPACT_MACRO_TAGS,
    TICK_SIZE_FLOAT,
    TOTAL_VOLUME_MIN,
    ZN_SEP26,
    weekly_volume_requirement,
)
from zn_competition.strategies.base import Side, Signal, StrategyContext

MIN_NET_EDGE_TICKS_RT = ZN_SEP26.dollars_to_ticks(ZN_SEP26.fee_round_turn, lots=1) + 0.25

MAX_CHURN_SPREAD_TICKS = 2.0
CHURN_LOT_SIZE = 1


@dataclass(frozen=True)
class ChurnLimitOrder:
    side: Side
    lots: int
    limit_price: float
    placed_at: str
    reason: str = "volume_churn_inside"


@dataclass(frozen=True)
class ChurnQuotePair:
    """Simultaneous 1-lot passive bid @ inside bid and ask @ inside ask (ZN Sep26)."""

    bid: ChurnLimitOrder
    ask: ChurnLimitOrder


@dataclass
class ChurnStepResult:
    fills: list[FillRecord] = field(default_factory=list)
    legs_executed_total: int = 0
    weekly_requirement: int = 0
    weekly_legs_remaining: int = 0
    quota_satisfied: bool = False
    active: bool = False
    action: str = "idle"


@dataclass
class VolumeChurner:
    """
    Secondary volume sleeve — only when net directional risk is zero (flat).

    Posts offsetting 1-lot limits on the inside bid and inside ask, counts every
    executed leg (entries and exits), and disables once the weekly lot quota is met.
    """

    name: str = "volume_churner"
    churn_lots: int = CHURN_LOT_SIZE
    max_spread_ticks: float = MAX_CHURN_SPREAD_TICKS

    _bid_working: ChurnLimitOrder | None = field(default=None, init=False, repr=False)
    _ask_working: ChurnLimitOrder | None = field(default=None, init=False, repr=False)
    _enabled: bool = field(default=True, init=False, repr=False)
    _session_legs_executed: int = field(default=0, init=False, repr=False)

    def __post_init__(self) -> None:
        if self.churn_lots < 1 or self.churn_lots > ZN_SEP26.max_position_lots:
            raise ValueError(
                f"churn_lots must be 1–{ZN_SEP26.max_position_lots}, got {self.churn_lots}"
            )

    def reset(self) -> None:
        self._bid_working = None
        self._ask_working = None
        self._enabled = True
        self._session_legs_executed = 0

    @property
    def legs_executed(self) -> int:
        return self._session_legs_executed

    @classmethod
    def place_offsetting_inside_quotes(
        cls,
        book: OrderBookSnapshot,
        timestamp: str,
        lots: int = CHURN_LOT_SIZE,
    ) -> ChurnQuotePair:
        """
        Build simultaneous passive limits at the inside market on ZN Sep26.

        - Buy  ``lots`` @ inside bid (join bid)
        - Sell ``lots`` @ inside ask (join offer)

        Caller must only invoke when ``position == 0``; each leg is validated separately
        against the ±10 lot cap before live deployment.
        """
        if lots < 1 or lots > ZN_SEP26.max_position_lots:
            raise ValueError(
                f"lots must be 1–{ZN_SEP26.max_position_lots}, got {lots}"
            )
        bid_price = book.inside_bid
        ask_price = book.inside_ask
        if ask_price <= bid_price:
            raise ValueError(
                f"invalid inside market: bid {bid_price} >= ask {ask_price}"
            )

        validate_order(0, OrderRequest(Side.BUY, lots, reason="volume_churn_bid"))
        validate_order(0, OrderRequest(Side.SELL, lots, reason="volume_churn_ask"))

        return ChurnQuotePair(
            bid=ChurnLimitOrder(
                side=Side.BUY,
                lots=lots,
                limit_price=bid_price,
                placed_at=timestamp,
                reason="volume_churn_bid",
            ),
            ask=ChurnLimitOrder(
                side=Side.SELL,
                lots=lots,
                limit_price=ask_price,
                placed_at=timestamp,
                reason="volume_churn_ask",
            ),
        )

    def weekly_requirement(self, week_number: int) -> int:
        return weekly_volume_requirement(week_number)

    def _legs_traded_this_week(self, ctx: StrategyContext, ledger: PositionLedger) -> int:
        return max(ctx.leg_lots_traded_this_week, ledger.leg_lots_traded)

    def weekly_quota_satisfied(
        self,
        ctx: StrategyContext,
        ledger: PositionLedger | None = None,
    ) -> bool:
        if ctx.weekly_min_remaining <= 0:
            return True
        required = self.weekly_requirement(ctx.week_number)
        legs = (
            self._legs_traded_this_week(ctx, ledger)
            if ledger is not None
            else ctx.leg_lots_traded_this_week
        )
        return legs >= required

    def competition_quota_satisfied(self, ctx: StrategyContext) -> bool:
        return ctx.leg_lots_traded_total >= TOTAL_VOLUME_MIN

    def should_run(self, ctx: StrategyContext) -> bool:
        if not self._enabled:
            return False
        if self.weekly_quota_satisfied(ctx):
            return False
        if ctx.position != 0:
            return False
        if ctx.event_tag in HIGH_IMPACT_MACRO_TAGS:
            return False
        if ctx.features is not None and ctx.features.spread_ticks > self.max_spread_ticks:
            return False
        return True

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        """Signal path: indicate churn readiness (execution via ``process_tick``)."""
        if not self.should_run(ctx):
            return None
        return Signal(
            side=Side.BUY,
            size=self.churn_lots,
            urgency="passive",
            reason="volume_churn_arm",
            expected_edge_ticks=-ZN_SEP26.dollars_to_ticks(
                ZN_SEP26.fee_round_turn, lots=1
            ),
            max_hold_seconds=5,
        )

    def process_tick(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        ctx: StrategyContext,
    ) -> ChurnStepResult:
        """
        Full churn execution for one quote update.

        Counts each ``FillRecord`` as leg lots toward the weekly quota (entry and exit
        legs both count). Stops placing new quotes when the week minimum is satisfied.
        """
        required = self.weekly_requirement(ctx.week_number)
        legs_done = self._legs_traded_this_week(ctx, ledger)
        remaining = max(0, required - legs_done)
        result = ChurnStepResult(
            weekly_requirement=required,
            weekly_legs_remaining=remaining,
            legs_executed_total=self._session_legs_executed,
        )

        if self.weekly_quota_satisfied(ctx, ledger) or self.competition_quota_satisfied(ctx):
            self._enabled = False
            self._bid_working = None
            self._ask_working = None
            result.quota_satisfied = True
            result.active = False
            result.action = "quota_off"
            return result

        if not self._enabled:
            result.action = "disabled"
            return result

        if ctx.event_tag in HIGH_IMPACT_MACRO_TAGS:
            result.action = "macro_blocked"
            return result

        spread_ticks = (book.ask - book.bid) / TICK_SIZE_FLOAT
        if spread_ticks > self.max_spread_ticks:
            result.action = "spread_blocked"
            return result

        if ledger.position != 0:
            flatten = self._flatten_inventory(quote, book, ledger)
            if flatten is not None:
                result.fills.append(flatten)
                self._record_leg(flatten.lots)
            result.action = "flatten"
            self._sync_quota(result, ctx, ledger)
            return result

        if not self.should_run(ctx):
            result.action = "idle"
            return result

        if self._bid_working is None and self._ask_working is None:
            pair = self.place_offsetting_inside_quotes(
                book, quote.timestamp, self.churn_lots
            )
            self._bid_working = pair.bid
            self._ask_working = pair.ask
            result.action = "quotes_placed"
            result.active = True

        bid_fill = self._try_fill_churn_order(
            self._bid_working, quote, book, ledger, Side.BUY
        )
        if bid_fill is not None:
            result.fills.append(bid_fill)
            self._record_leg(bid_fill.lots)
            self._bid_working = None

        ask_fill = self._try_fill_churn_order(
            self._ask_working, quote, book, ledger, Side.SELL
        )
        if ask_fill is not None:
            result.fills.append(ask_fill)
            self._record_leg(ask_fill.lots)
            self._ask_working = None

        if ledger.position != 0:
            flatten = self._flatten_inventory(quote, book, ledger)
            if flatten is not None:
                result.fills.append(flatten)
                self._record_leg(flatten.lots)
                result.action = "churn_cycle_flatten"
        elif bid_fill and ask_fill:
            result.action = "churn_cycle_complete"
        elif bid_fill or ask_fill:
            result.action = "churn_partial_fill"
        else:
            result.action = "quotes_working"

        result.active = self._enabled and not self.weekly_quota_satisfied(ctx, ledger)
        self._sync_quota(result, ctx, ledger)
        result.legs_executed_total = self._session_legs_executed
        return result

    def _record_leg(self, lots: int) -> None:
        self._session_legs_executed += lots

    def _sync_quota(
        self,
        result: ChurnStepResult,
        ctx: StrategyContext,
        ledger: PositionLedger,
    ) -> None:
        traded = self._legs_traded_this_week(ctx, ledger)
        required = self.weekly_requirement(ctx.week_number)
        result.weekly_legs_remaining = max(0, required - traded)
        if traded >= required or ctx.weekly_min_remaining <= 0:
            self._enabled = False
            self._bid_working = None
            self._ask_working = None
            result.quota_satisfied = True
            result.active = False

    def _try_fill_churn_order(
        self,
        order: ChurnLimitOrder | None,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        side: Side,
    ) -> FillRecord | None:
        if order is None:
            return None

        if side == Side.BUY:
            if abs(ZN_SEP26.round_price_to_tick(quote.bid) - order.limit_price) > 1e-9:
                return None
            lots = clip_order_size(order.lots, ledger.position)
            if lots <= 0:
                return None
            validate_order(ledger.position, OrderRequest(Side.BUY, lots, order.reason))
        else:
            if abs(ZN_SEP26.round_price_to_tick(quote.ask) - order.limit_price) > 1e-9:
                return None
            lots = clip_order_size(order.lots, ledger.position)
            if lots <= 0:
                return None
            validate_order(ledger.position, OrderRequest(Side.SELL, lots, order.reason))

        fill = FillRecord(
            side=side,
            lots=order.lots,
            price=order.limit_price,
            fee_usd=order.lots * FEE_PER_LOT_PER_SIDE_USD,
            timestamp=quote.timestamp,
            reason=order.reason,
        )
        ledger.apply_fill(fill)
        return fill

    def _flatten_inventory(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
    ) -> FillRecord | None:
        """Exit any churn inventory at the inside market (scratch to flat)."""
        pos = ledger.position
        if pos == 0:
            return None

        if pos > 0:
            side = Side.SELL
            price = book.inside_ask
            lots = min(abs(pos), self.churn_lots)
            validate_order(pos, OrderRequest(Side.SELL, lots, "volume_churn_flatten"))
        else:
            side = Side.BUY
            price = book.inside_bid
            lots = min(abs(pos), self.churn_lots)
            validate_order(pos, OrderRequest(Side.BUY, lots, "volume_churn_flatten"))

        fill = FillRecord(
            side=side,
            lots=lots,
            price=price,
            fee_usd=lots * FEE_PER_LOT_PER_SIDE_USD,
            timestamp=quote.timestamp,
            reason="volume_churn_flatten",
        )
        ledger.apply_fill(fill)
        return fill

    def expected_scratch_cost_usd(self, lots: int | None = None) -> float:
        """Round-turn fee drag for a completed churn cycle at zero tick move."""
        n = lots if lots is not None else self.churn_lots
        return net_pnl_from_tick_move(0.0, n, sides=2).net_pnl_usd


class SessionMeanReversionStrategy:
    name = "session_mean_reversion"

    def __init__(
        self,
        entry_z: float = 1.25,
        exit_z: float = 0.35,
        size: int = 2,
        max_hold_seconds: int = 900,
        min_session_tag: str = "high_liquidity",
        enable_volume_churn: bool = True,
        volume_churner: VolumeChurner | None = None,
    ) -> None:
        if entry_z <= exit_z:
            raise ValueError("entry_z must exceed exit_z")
        if size < 1 or size > ZN_SEP26.max_position_lots:
            raise ValueError(f"size must be 1–{ZN_SEP26.max_position_lots}")
        self.entry_z = entry_z
        self.exit_z = exit_z
        self.size = size
        self.max_hold_seconds = max_hold_seconds
        self.min_session_tag = min_session_tag
        self.enable_volume_churn = enable_volume_churn
        self.volume_churner = volume_churner or VolumeChurner()

    def on_tick(self, ctx: StrategyContext) -> Signal | None:
        signal = self._mean_reversion_signal(ctx)
        if signal is not None:
            return signal
        if self.enable_volume_churn and ctx.position == 0:
            return self.volume_churner.on_tick(ctx)
        return None

    def process_churn_tick(
        self,
        quote: Quote,
        book: OrderBookSnapshot,
        ledger: PositionLedger,
        ctx: StrategyContext,
    ) -> ChurnStepResult | None:
        """Run volume churner when flat and MR has no position on."""
        if not self.enable_volume_churn:
            return None
        if ctx.position != 0:
            return None
        return self.volume_churner.process_tick(quote, book, ledger, ctx)

    def _mean_reversion_signal(self, ctx: StrategyContext) -> Signal | None:
        if ctx.event_tag in HIGH_IMPACT_MACRO_TAGS:
            return None
        if ctx.features is None:
            return None
        if ctx.features.session_tag != self.min_session_tag:
            return None
        if ctx.features.spread_ticks > 2.0:
            return None

        z = ctx.features.vwap_z
        abs_z = abs(z)

        if ctx.position != 0 and abs_z < self.exit_z:
            return Signal(
                side=Side.FLAT,
                size=abs(ctx.position),
                urgency="aggressive",
                reason="mr_exit_vwap",
                expected_edge_ticks=0.5,
                max_hold_seconds=self.max_hold_seconds,
            )

        if abs_z < self.entry_z:
            return None

        side = Side.SELL if z > 0 else Side.BUY
        lots = min(self.size, ZN_SEP26.max_position_lots - abs(ctx.position))
        if lots <= 0:
            return None

        expected_ticks = min(abs(z * 0.4), 2.5)
        signal = Signal(
            side=side,
            size=lots,
            urgency="passive",
            reason="mr_enter_vwap",
            expected_edge_ticks=expected_ticks,
            max_hold_seconds=self.max_hold_seconds,
        )
        if signal.net_edge_after_round_turn_fee_ticks(lots) < MIN_NET_EDGE_TICKS_RT:
            return None
        return signal
