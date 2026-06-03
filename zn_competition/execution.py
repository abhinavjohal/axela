"""
Simulated execution on ZN tick grid with per-side fees.
"""

from __future__ import annotations

from zn_competition.microstructure import Quote, aggressive_fill_price, passive_fill_price
from zn_competition.risk import FillRecord, OrderRequest, clip_order_size, validate_order
from zn_competition.specs import FEE_PER_LOT_PER_SIDE_USD, ZN_SEP26
from zn_competition.strategies.base import Side, Signal


def signal_to_order(signal: Signal, position: int) -> OrderRequest | None:
    if signal.side == Side.FLAT:
        if position == 0:
            return None
        return OrderRequest(side=Side.FLAT, lots=abs(position), reason=signal.reason)
    lots = clip_order_size(signal.size, position)
    if lots <= 0:
        return None
    return OrderRequest(side=signal.side, lots=lots, reason=signal.reason)


def execute_order(
    order: OrderRequest,
    quote: Quote,
    position: int,
    urgency: str,
) -> FillRecord:
    validate_order(position, order)
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
    order = signal_to_order(signal, position)
    if order is None:
        return None
    return execute_order(order, quote, position, signal.urgency)
