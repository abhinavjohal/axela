"""
Position ledger, fee accounting ($0.50/side), and loss limits (max 10 lots).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD, MAX_POSITION_LOTS, ZN_SEP26
from zn_competition.strategies.base import Side


class ExecutionException(Exception):
    """Raised when an order would violate competition execution rules (e.g. position cap)."""


@dataclass(frozen=True)
class OrderRequest:
    side: Side
    lots: int
    reason: str = ""


@dataclass
class RiskState:
    daily_loss_limit_usd: float = 1_500.0
    daily_realized_pnl_usd: float = 0.0
    halted: bool = False
    halt_reason: str = ""

    def apply_realized(self, pnl_usd: float) -> None:
        self.daily_realized_pnl_usd += pnl_usd
        if self.daily_realized_pnl_usd <= -self.daily_loss_limit_usd:
            self.halted = True
            self.halt_reason = (
                f"daily loss limit {self.daily_loss_limit_usd:.2f} USD breached; "
                f"realized_pnl={self.daily_realized_pnl_usd:.2f}"
            )


def clip_order_size(requested: int, position: int) -> int:
    """Size orders internally; use ``enforce_order_size`` at execution to reject breaches."""
    if requested < 0:
        raise ValueError(f"requested lots must be non-negative, got {requested}")
    if abs(position) > MAX_POSITION_LOTS:
        raise ExecutionException(
            f"POSITION_CAP: current position {position} already exceeds "
            f"maximum {MAX_POSITION_LOTS} lots"
        )
    room = MAX_POSITION_LOTS - abs(position)
    return max(0, min(requested, room))


def enforce_order_size(requested: int, position: int) -> int:
    """
    Require the full requested size or raise ``ExecutionException``.

    Silent clipping is not allowed on the execution path.
    """
    if requested <= 0:
        raise ExecutionException(
            f"ORDER_REJECTED: lot size must be positive, got {requested}"
        )
    if abs(position) > MAX_POSITION_LOTS:
        raise ExecutionException(
            f"POSITION_CAP: current position {position} exceeds "
            f"maximum {MAX_POSITION_LOTS} lots"
        )
    room = MAX_POSITION_LOTS - abs(position)
    if requested > room:
        raise ExecutionException(
            f"POSITION_CAP: order size {requested} would breach the {MAX_POSITION_LOTS}-lot "
            f"cap (position={position}, available_room={room})"
        )
    return requested


def projected_position(current: int, side: Side, lots: int) -> int:
    if lots < 0:
        raise ValueError(f"lots must be non-negative, got {lots}")
    if side == Side.BUY:
        return current + lots
    if side == Side.SELL:
        return current - lots
    if side == Side.FLAT:
        if lots != abs(current):
            raise ValueError(f"FLAT lots {lots} must equal |position| {abs(current)}")
        return 0
    raise ValueError(f"unknown side {side}")


def validate_order(current_position: int, order: OrderRequest) -> None:
    if order.side in (Side.BUY, Side.SELL) and order.lots <= 0:
        raise ValueError("BUY/SELL require positive lots")
    if order.side == Side.FLAT and order.lots != abs(current_position):
        raise ValueError(
            f"FLAT order lots {order.lots} must equal |position| {abs(current_position)}"
        )
    if order.side != Side.FLAT:
        new_pos = projected_position(current_position, order.side, order.lots)
        if abs(new_pos) > MAX_POSITION_LOTS:
            raise ExecutionException(
                f"POSITION_CAP: order would move position {current_position} -> {new_pos} "
                f"(maximum absolute size {MAX_POSITION_LOTS})"
            )


@dataclass(frozen=True)
class FillRecord:
    side: Side
    lots: int
    price: float
    fee_usd: float
    timestamp: str
    reason: str


@dataclass
class PositionLedger:
    position: int = 0
    avg_entry_price: float = 0.0
    leg_lots_traded: int = 0
    total_fees_usd: float = 0.0
    realized_pnl_usd: float = 0.0
    fills: list[FillRecord] = field(default_factory=list)

    def mark_price_pnl_usd(self, mark: float) -> float:
        if self.position == 0:
            return 0.0
        if self.position > 0:
            ticks = ZN_SEP26.price_delta_to_ticks(mark - self.avg_entry_price)
        else:
            ticks = ZN_SEP26.price_delta_to_ticks(self.avg_entry_price - mark)
        return ZN_SEP26.ticks_to_dollars(ticks, abs(self.position))

    def apply_fill(self, fill: FillRecord) -> float:
        if fill.lots <= 0:
            raise ValueError(f"fill lots must be positive, got {fill.lots}")

        fee = fill.lots * FEE_PER_LOT_PER_SIDE_USD
        self.total_fees_usd += fee
        self.leg_lots_traded += fill.lots
        self.fills.append(fill)

        if fill.side == Side.BUY:
            gross = self._process_buy(fill.lots, fill.price)
        elif fill.side == Side.SELL:
            gross = self._process_sell(fill.lots, fill.price)
        elif fill.side == Side.FLAT:
            gross = self._process_flat(fill.lots, fill.price)
        else:
            raise ValueError(f"unsupported side {fill.side}")

        net = gross - fee
        self.realized_pnl_usd += net
        return net

    def _process_buy(self, lots: int, price: float) -> float:
        realized = 0.0
        remaining = lots
        if self.position < 0:
            cover = min(remaining, abs(self.position))
            realized += self._close_short(cover, price)
            self.position += cover
            remaining -= cover
        if remaining > 0:
            if self.position > 0:
                new_pos = self.position + remaining
                self.avg_entry_price = (
                    self.avg_entry_price * self.position + price * remaining
                ) / new_pos
            else:
                self.avg_entry_price = price
            self.position += remaining
        return realized

    def _process_sell(self, lots: int, price: float) -> float:
        realized = 0.0
        remaining = lots
        if self.position > 0:
            close_qty = min(remaining, self.position)
            realized += self._close_long(close_qty, price)
            self.position -= close_qty
            remaining -= close_qty
        if remaining > 0:
            if self.position < 0:
                short_size = abs(self.position)
                new_short = short_size + remaining
                self.avg_entry_price = (
                    self.avg_entry_price * short_size + price * remaining
                ) / new_short
            else:
                self.avg_entry_price = price
            self.position -= remaining
        return realized

    def _process_flat(self, lots: int, price: float) -> float:
        if self.position > 0:
            close_qty = min(lots, self.position)
            gross = self._close_long(close_qty, price)
            self.position -= close_qty
        elif self.position < 0:
            close_qty = min(lots, abs(self.position))
            gross = self._close_short(close_qty, price)
            self.position += close_qty
        else:
            gross = 0.0
        if self.position == 0:
            self.avg_entry_price = 0.0
        return gross

    def _close_long(self, lots: int, price: float) -> float:
        ticks = ZN_SEP26.price_delta_to_ticks(price - self.avg_entry_price)
        return ZN_SEP26.ticks_to_dollars(ticks, lots)

    def _close_short(self, lots: int, price: float) -> float:
        ticks = ZN_SEP26.price_delta_to_ticks(self.avg_entry_price - price)
        return ZN_SEP26.ticks_to_dollars(ticks, lots)
