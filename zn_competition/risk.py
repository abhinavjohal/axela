"""
Position ledger, fee accounting ($0.50/side), and loss limits (max 10 lots).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD, MAX_POSITION_LOTS, ZN_SEP26
from zn_competition.strategies.base import Side

logger = logging.getLogger(__name__)


class ExecutionException(Exception):
    """Raised when an order would violate competition execution rules."""


class ExecutionRiskException(ExecutionException):
    """Order blocked by competition risk gate (position cap, halted, etc.)."""


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


def get_current_position(ledger: PositionLedger) -> int:
    """Read signed net position from the ledger (source of truth for execution)."""
    return ledger.position


def assert_ledger_position_valid(ledger: PositionLedger) -> None:
    """Ensure ledger position is within competition cap before any submission."""
    position = get_current_position(ledger)
    if abs(position) > MAX_POSITION_LOTS:
        msg = (
            f"POSITION_CAP: ledger position {position} exceeds maximum "
            f"{MAX_POSITION_LOTS} lots"
        )
        logger.error("EXECUTION_RISK: %s", msg)
        raise ExecutionRiskException(msg)


def signed_incoming_lots(side: Side, lots: int, current_position: int) -> int:
    """
    Signed lot change applied by an order (for position + delta gate).

    BUY: +lots, SELL: -lots, FLAT: delta that zeros position.
    """
    if lots < 0:
        raise ValueError(f"lots must be non-negative, got {lots}")
    if side == Side.BUY:
        return lots
    if side == Side.SELL:
        return -lots
    if side == Side.FLAT:
        if current_position == 0:
            return 0
        if lots != abs(current_position):
            raise ValueError(
                f"FLAT lots {lots} must equal |position| {abs(current_position)}"
            )
        return -current_position
    raise ValueError(f"unknown side {side}")


def gate_order_submission(
    current_position: int,
    incoming_order_lots: int,
    side: Side,
) -> None:
    """
    Strict competition gate before the execution engine accepts an order.

    Delegates to ``PositionGuard`` (TT ADL PositionRisk + LessThan_Guard parity).
    Blocks when ``ABS(current_position + signed_incoming_lots) > MAX_POSITION_LOTS``.
    """
    _DEFAULT_POSITION_GUARD.validate_submission(
        current_position, incoming_order_lots, side
    )


@dataclass
class PositionGuard:
    """
    Tracks signed net position and enforces the ±10-lot cap before routing.

    Python mirror of TT ADL **PositionRisk Block** + **LessThan_Guard** (see
    ``TT_ADL_SPECIFICATION.md`` §4.0): projected exposure must satisfy
    ``ABS(net_position + signed_incoming) <= max_position_lots``.
    """

    max_position_lots: int = MAX_POSITION_LOTS
    _net_position: int = 0

    def __post_init__(self) -> None:
        if self.max_position_lots < 1:
            raise ValueError(
                f"max_position_lots must be >= 1, got {self.max_position_lots}"
            )

    @property
    def net_position(self) -> int:
        """Signed net lots (long positive, short negative)."""
        return self._net_position

    @property
    def abs_net_position(self) -> int:
        """Absolute net exposure — ``ABS(net_position)``."""
        return abs(self._net_position)

    def sync_from_ledger(self, ledger: PositionLedger) -> int:
        """Refresh internal position from ``PositionLedger`` (exchange truth)."""
        self._net_position = ledger.position
        return self._net_position

    def projected_net_position(
        self,
        incoming_lots: int,
        side: Side,
        *,
        current_position: int | None = None,
    ) -> int:
        """Net position after applying ``incoming_lots`` on ``side``."""
        base = self._net_position if current_position is None else current_position
        signed = signed_incoming_lots(side, incoming_lots, base)
        return base + signed

    def less_than_guard_passes(
        self,
        incoming_lots: int,
        side: Side,
        *,
        current_position: int | None = None,
    ) -> bool:
        """
        ADL LessThan_Guard: ``ABS(projected_net) <= max_position_lots``.
        """
        projected = self.projected_net_position(
            incoming_lots, side, current_position=current_position
        )
        return abs(projected) <= self.max_position_lots

    def validate_submission(
        self,
        current_position: int,
        incoming_lots: int,
        side: Side,
    ) -> None:
        """
        Raise ``ExecutionRiskException`` if order would breach the position cap.

        Matches ADL check before Discrete Order routing::
            ABS(current_position + signed_incoming_lots) > max → cancel
        """
        if incoming_lots <= 0 and side != Side.FLAT:
            msg = f"ORDER_REJECTED: lot size must be positive, got {incoming_lots}"
            logger.error("POSITION_GUARD: %s", msg)
            raise ExecutionRiskException(msg)

        if abs(current_position) > self.max_position_lots:
            msg = (
                f"POSITION_GUARD: current net position {current_position} exceeds "
                f"maximum {self.max_position_lots} lots"
            )
            logger.error("POSITION_GUARD: %s", msg)
            raise ExecutionRiskException(msg)

        signed = signed_incoming_lots(side, incoming_lots, current_position)
        projected = current_position + signed

        if abs(projected) > self.max_position_lots:
            msg = (
                f"POSITION_GUARD: LessThan_Guard failed — "
                f"ABS({current_position} + {signed}) = {abs(projected)} > "
                f"{self.max_position_lots} "
                f"(side={side.value}, incoming_lots={incoming_lots})"
            )
            logger.error("POSITION_GUARD: %s — order canceled before routing", msg)
            raise ExecutionRiskException(msg)

    def record_fill(self, side: Side, lots: int, position_before: int) -> None:
        """Update tracked net position after a confirmed fill."""
        self._net_position = position_before + signed_incoming_lots(
            side, lots, position_before
        )


_DEFAULT_POSITION_GUARD = PositionGuard()


def get_position_guard() -> PositionGuard:
    """Shared guard instance used by ``gate_order_submission``."""
    return _DEFAULT_POSITION_GUARD


def clip_order_size(requested: int, position: int) -> int:
    """Size orders internally; execution path uses ``enforce_order_size`` / gate."""
    if requested < 0:
        raise ValueError(f"requested lots must be non-negative, got {requested}")
    if abs(position) > MAX_POSITION_LOTS:
        raise ExecutionRiskException(
            f"POSITION_CAP: current position {position} already exceeds "
            f"maximum {MAX_POSITION_LOTS} lots"
        )
    room = MAX_POSITION_LOTS - abs(position)
    return max(0, min(requested, room))


def enforce_order_size(requested: int, position: int, side: Side) -> int:
    """Require full size or raise ``ExecutionRiskException`` (no silent clip)."""
    gate_order_submission(position, requested, side)
    return requested


def projected_position(current: int, side: Side, lots: int) -> int:
    if lots < 0:
        raise ValueError(f"lots must be non-negative, got {lots}")
    return current + signed_incoming_lots(side, lots, current)


def validate_order(current_position: int, order: OrderRequest) -> None:
    if order.side in (Side.BUY, Side.SELL) and order.lots <= 0:
        raise ValueError("BUY/SELL require positive lots")
    if order.side == Side.FLAT and order.lots != abs(current_position):
        raise ValueError(
            f"FLAT order lots {order.lots} must equal |position| {abs(current_position)}"
        )
    gate_order_submission(current_position, order.lots, order.side)


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
