"""
Simulated execution on ZN tick grid with per-side fees and competition safety gates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from uuid import uuid4

from zn_competition.microstructure import Quote, aggressive_fill_price, passive_fill_price
from zn_competition.risk import (
    ExecutionRiskException,
    FillRecord,
    OrderRequest,
    PositionLedger,
    assert_ledger_position_valid,
    gate_order_submission,
    get_current_position,
    validate_order,
)
from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD, ZN_SEP26
from zn_competition.strategies.base import Side, Signal

logger = logging.getLogger(__name__)


class StrategyRegime(str, Enum):
    """Execution state-machine regimes for opposing-order purge."""

    TREND_FOLLOWING = "trend_following"
    MEAN_REVERSION = "mean_reversion"
    NEUTRAL = "neutral"


@dataclass
class WorkingOrder:
    """Passive/working order tracked before exchange fill (sim layer)."""

    order_id: str
    request: OrderRequest
    regime: StrategyRegime
    strategy_name: str
    urgency: str
    timestamp: str
    canceled: bool = False

    @property
    def is_active(self) -> bool:
        return not self.canceled


@dataclass
class PurgeReport:
    canceled_count: int
    canceled_order_ids: list[str]
    purged_regime: StrategyRegime


@dataclass
class ExecutionEngine:
    """
    Core execution path: risk gate → working-order book → fill simulation.

    All submissions read ``ledger.position`` from ``risk.py`` before sending.
    """

    _working_orders: dict[str, WorkingOrder] = field(default_factory=dict)

    def active_working_orders(self) -> list[WorkingOrder]:
        return [o for o in self._working_orders.values() if o.is_active]

    def register_working_order(
        self,
        request: OrderRequest,
        regime: StrategyRegime,
        strategy_name: str,
        urgency: str,
        timestamp: str,
    ) -> WorkingOrder:
        order_id = str(uuid4())
        working = WorkingOrder(
            order_id=order_id,
            request=request,
            regime=regime,
            strategy_name=strategy_name,
            urgency=urgency,
            timestamp=timestamp,
        )
        self._working_orders[order_id] = working
        return working

    def cancel_order(self, order_id: str) -> bool:
        order = self._working_orders.get(order_id)
        if order is None or order.canceled:
            return False
        order.canceled = True
        logger.info(
            "EXECUTION_CANCEL: order_id=%s regime=%s reason=%s",
            order_id,
            order.regime.value,
            order.request.reason,
        )
        return True

    def purge_regime(self, regime: StrategyRegime) -> PurgeReport:
        """Instantly cancel all active working orders for a strategy regime."""
        canceled_ids: list[str] = []
        for order_id, order in list(self._working_orders.items()):
            if order.is_active and order.regime == regime:
                order.canceled = True
                canceled_ids.append(order_id)
                logger.info(
                    "EXECUTION_PURGE: order_id=%s regime=%s strategy=%s reason=%s",
                    order_id,
                    regime.value,
                    order.strategy_name,
                    order.request.reason,
                )
        return PurgeReport(
            canceled_count=len(canceled_ids),
            canceled_order_ids=canceled_ids,
            purged_regime=regime,
        )

    def purge_opposing_regime(self, active_regime: StrategyRegime) -> PurgeReport | None:
        """Cancel working orders from the regime opposite to ``active_regime``."""
        if active_regime == StrategyRegime.TREND_FOLLOWING:
            return self.purge_regime(StrategyRegime.MEAN_REVERSION)
        if active_regime == StrategyRegime.MEAN_REVERSION:
            return self.purge_regime(StrategyRegime.TREND_FOLLOWING)
        return None

    def submit_order(
        self,
        ledger: PositionLedger,
        order: OrderRequest,
        quote: Quote,
        urgency: str,
        *,
        regime: StrategyRegime = StrategyRegime.NEUTRAL,
        strategy_name: str = "",
        register_working: bool = False,
    ) -> FillRecord:
        """
        Submit one order through the strict risk gate, then simulate fill.

        Reads current position from ``ledger`` before any action.
        """
        assert_ledger_position_valid(ledger)
        current_position = get_current_position(ledger)

        gate_order_submission(current_position, order.lots, order.side)
        validate_order(current_position, order)

        if register_working and order.side in (Side.BUY, Side.SELL) and urgency == "passive":
            self.register_working_order(
                order, regime, strategy_name, urgency, quote.timestamp
            )

        fill = execute_order(order, quote, current_position, urgency)
        ledger.apply_fill(fill)
        return fill

    def submit_signal(
        self,
        ledger: PositionLedger,
        signal: Signal,
        quote: Quote,
        *,
        regime: StrategyRegime = StrategyRegime.NEUTRAL,
        strategy_name: str = "",
    ) -> FillRecord | None:
        order = signal_to_order(signal, get_current_position(ledger))
        if order is None:
            return None
        register_working = signal.urgency == "passive" and signal.side in (Side.BUY, Side.SELL)
        return self.submit_order(
            ledger,
            order,
            quote,
            signal.urgency,
            regime=regime,
            strategy_name=strategy_name,
            register_working=register_working,
        )


def signal_to_order(signal: Signal, position: int) -> OrderRequest | None:
    if signal.side == Side.FLAT:
        if position == 0:
            return None
        return OrderRequest(side=Side.FLAT, lots=abs(position), reason=signal.reason)
    from zn_competition.risk import enforce_order_size

    lots = enforce_order_size(signal.size, position, signal.side)
    return OrderRequest(side=signal.side, lots=lots, reason=signal.reason)


def execute_order(
    order: OrderRequest,
    quote: Quote,
    position: int,
    urgency: str,
) -> FillRecord:
    """Build fill after gate passed (internal — use ``ExecutionEngine.submit_order``)."""
    if order.side == Side.FLAT:
        side_str = "SELL" if position > 0 else "BUY"
        price = (
            aggressive_fill_price(side_str, quote)
            if urgency == "aggressive"
            else passive_fill_price(side_str, quote)
        )
        return FillRecord(
            side=Side.FLAT,
            lots=order.lots,
            price=ZN_SEP26.round_price_to_tick(price),
            fee_usd=order.lots * FEE_PER_LOT_PER_SIDE_USD,
            timestamp=quote.timestamp,
            reason=order.reason,
        )

    side_str = order.side.value
    if urgency == "aggressive":
        price = aggressive_fill_price(side_str, quote)
    elif urgency == "passive":
        price = passive_fill_price(side_str, quote)
    else:
        raise ValueError(f"unknown urgency: {urgency}")

    return FillRecord(
        side=order.side,
        lots=order.lots,
        price=ZN_SEP26.round_price_to_tick(price),
        fee_usd=order.lots * FEE_PER_LOT_PER_SIDE_USD,
        timestamp=quote.timestamp,
        reason=order.reason,
    )


def execute_signal(
    signal: Signal,
    quote: Quote,
    position: int,
) -> FillRecord | None:
    """Legacy path: gate then fill (position supplied by caller)."""
    order = signal_to_order(signal, position)
    if order is None:
        return None
    gate_order_submission(position, order.lots, order.side)
    validate_order(position, order)
    return execute_order(order, quote, position, signal.urgency)
