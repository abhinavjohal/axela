"""
ZN market microstructure utilities — tick grid, spread, book imbalance.
Standard library only.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from zn_competition.specs import TICK_SIZE_FLOAT, ZN_SEP26


@dataclass(frozen=True)
class Quote:
    timestamp: str
    bid: float
    ask: float
    bid_size: int = 0
    ask_size: int = 0
    bid_l2_size: int = 0
    ask_l2_size: int = 0
    last: float | None = None
    volume: int = 0

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError(f"bid/ask must be positive: bid={self.bid}, ask={self.ask}")
        if self.ask < self.bid:
            raise ValueError(f"crossed market: bid={self.bid}, ask={self.ask}")
        if self.bid_size < 0 or self.ask_size < 0:
            raise ValueError("sizes must be non-negative")
        if self.bid_l2_size < 0 or self.ask_l2_size < 0:
            raise ValueError("L2 sizes must be non-negative")

    @property
    def mid(self) -> float:
        if self.last is not None and self.bid <= self.last <= self.ask:
            return self.last
        return (self.bid + self.ask) / 2.0

    @property
    def spread_ticks(self) -> float:
        return (self.ask - self.bid) / TICK_SIZE_FLOAT

    def round_mid_to_tick(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.mid)


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Level 1 + Level 2 quantities at inside market (TT depth)."""

    timestamp: str
    bid: float
    ask: float
    bid_l1_size: int
    ask_l1_size: int
    bid_l2_size: int
    ask_l2_size: int

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError(f"bid/ask must be positive: bid={self.bid}, ask={self.ask}")
        if self.ask < self.bid:
            raise ValueError(f"crossed market: bid={self.bid}, ask={self.ask}")
        for name, size in (
            ("bid_l1_size", self.bid_l1_size),
            ("ask_l1_size", self.ask_l1_size),
            ("bid_l2_size", self.bid_l2_size),
            ("ask_l2_size", self.ask_l2_size),
        ):
            if size < 0:
                raise ValueError(f"{name} must be non-negative, got {size}")

    @property
    def inside_bid(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.bid)

    @property
    def inside_ask(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.ask)


def calculate_order_book_imbalance(book: OrderBookSnapshot) -> float:
    """
    Order Book Imbalance (OBI) using L1 + L2 bid/ask displayed quantity.

    OBI = (Q_bid_L1 + Q_bid_L2 - Q_ask_L1 - Q_ask_L2) / (Q_bid_L1 + Q_bid_L2 + Q_ask_L1 + Q_ask_L2)

    Returns a value in [-1, 1]. Positive = bid-heavy (buy pressure).
    """
    bid_qty = book.bid_l1_size + book.bid_l2_size
    ask_qty = book.ask_l1_size + book.ask_l2_size
    total = bid_qty + ask_qty
    if total <= 0:
        return 0.0
    return (bid_qty - ask_qty) / total


def order_book_from_quote(quote: Quote) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        timestamp=quote.timestamp,
        bid=quote.bid,
        ask=quote.ask,
        bid_l1_size=quote.bid_size,
        ask_l1_size=quote.ask_size,
        bid_l2_size=quote.bid_l2_size,
        ask_l2_size=quote.ask_l2_size,
    )


@dataclass(frozen=True)
class FeatureSnapshot:
    timestamp: str
    mid: float
    vwap: float
    vwap_z: float
    book_imbalance: float
    realized_vol_ticks_1h: float
    spread_ticks: float
    session_tag: str


def book_imbalance(bid_size: int, ask_size: int) -> float:
    """L1-only imbalance in [-1, 1]. Prefer calculate_order_book_imbalance for L1+L2."""
    total = bid_size + ask_size
    if total <= 0:
        return 0.0
    return (bid_size - ask_size) / total


def passive_fill_price(side: str, quote: Quote) -> float:
    if side == "BUY":
        return quote.bid
    if side == "SELL":
        return quote.ask
    raise ValueError(f"unknown side: {side}")


def aggressive_fill_price(side: str, quote: Quote) -> float:
    if side == "BUY":
        return quote.ask
    if side == "SELL":
        return quote.bid
    raise ValueError(f"unknown side: {side}")


class RollingStdTicks:
    """Rolling std-dev of mid changes in ticks (Welford online)."""

    def __init__(self, window: int) -> None:
        if window < 2:
            raise ValueError(f"window must be >= 2, got {window}")
        self._window = window
        self._deltas: list[float] = []

    def update(self, mid: float, prev_mid: float | None) -> float:
        if prev_mid is not None and prev_mid > 0:
            delta_ticks = (mid - prev_mid) / TICK_SIZE_FLOAT
            self._deltas.append(delta_ticks)
            if len(self._deltas) > self._window:
                self._deltas.pop(0)
        if len(self._deltas) < 2:
            return max(TICK_SIZE_FLOAT, 1.0)
        mean = sum(self._deltas) / len(self._deltas)
        var = sum((x - mean) ** 2 for x in self._deltas) / (len(self._deltas) - 1)
        return max(sqrt(var), 0.5)
