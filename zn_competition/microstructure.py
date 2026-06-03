"""
ZN market microstructure utilities — tick grid, spread, order book imbalance.
Standard library only. All tick math uses ``specs.TICK_SIZE_FLOAT`` (0.015625).
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
            raise ValueError("L1 sizes must be non-negative")
        if self.bid_l2_size < 0 or self.ask_l2_size < 0:
            raise ValueError("L2 sizes must be non-negative")

    @property
    def mid(self) -> float:
        if self.last is not None and self.bid <= self.last <= self.ask:
            return self.last
        return (self.bid + self.ask) / 2.0

    @property
    def spread_ticks(self) -> float:
        return ZN_SEP26.price_delta_to_ticks(self.ask - self.bid)

    def round_mid_to_tick(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.mid)


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Level 1 + Level 2 displayed quantities at the inside market."""

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
    def bid_qty(self) -> int:
        """Aggregate bid displayed size: Level 1 + Level 2."""
        return self.bid_l1_size + self.bid_l2_size

    @property
    def ask_qty(self) -> int:
        """Aggregate ask displayed size: Level 1 + Level 2."""
        return self.ask_l1_size + self.ask_l2_size

    @property
    def inside_bid(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.bid)

    @property
    def inside_ask(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.ask)

    @property
    def spread_ticks(self) -> float:
        return ZN_SEP26.price_delta_to_ticks(self.ask - self.bid)


@dataclass(frozen=True)
class OrderBookImbalanceResult:
    """Standardized OBI output from L1+L2 depth."""

    obi: float
    bid_qty: int
    ask_qty: int
    bid_l1_size: int
    ask_l1_size: int
    bid_l2_size: int
    ask_l2_size: int

    @property
    def total_qty(self) -> int:
        return self.bid_qty + self.ask_qty


class OrderBookImbalanceCalculator:
    """
    Computes the standardized Order Book Imbalance ratio from L1 and L2 quantities.

    Formula (Bid_Qty and Ask_Qty are L1+L2 aggregates)::

        OBI = (Bid_Qty - Ask_Qty) / (Bid_Qty + Ask_Qty)

    Range: [-1, 1]. Positive values indicate bid-side (buy) pressure.
    Returns 0.0 when total displayed quantity is zero.
    """

    @staticmethod
    def from_quantities(
        bid_l1_size: int,
        ask_l1_size: int,
        bid_l2_size: int = 0,
        ask_l2_size: int = 0,
    ) -> OrderBookImbalanceResult:
        for name, val in (
            ("bid_l1_size", bid_l1_size),
            ("ask_l1_size", ask_l1_size),
            ("bid_l2_size", bid_l2_size),
            ("ask_l2_size", ask_l2_size),
        ):
            if val < 0:
                raise ValueError(f"{name} must be non-negative, got {val}")

        bid_qty = bid_l1_size + bid_l2_size
        ask_qty = ask_l1_size + ask_l2_size
        total = bid_qty + ask_qty
        if total <= 0:
            obi = 0.0
        else:
            obi = (bid_qty - ask_qty) / total

        return OrderBookImbalanceResult(
            obi=obi,
            bid_qty=bid_qty,
            ask_qty=ask_qty,
            bid_l1_size=bid_l1_size,
            ask_l1_size=ask_l1_size,
            bid_l2_size=bid_l2_size,
            ask_l2_size=ask_l2_size,
        )

    @classmethod
    def from_snapshot(cls, book: OrderBookSnapshot) -> OrderBookImbalanceResult:
        return cls.from_quantities(
            book.bid_l1_size,
            book.ask_l1_size,
            book.bid_l2_size,
            book.ask_l2_size,
        )

    @classmethod
    def from_quote(cls, quote: Quote) -> OrderBookImbalanceResult:
        return cls.from_quantities(
            quote.bid_size,
            quote.ask_size,
            quote.bid_l2_size,
            quote.ask_l2_size,
        )


def calculate_order_book_imbalance(book: OrderBookSnapshot) -> float:
    """Return OBI ratio for an ``OrderBookSnapshot`` (L1+L2)."""
    return OrderBookImbalanceCalculator.from_snapshot(book).obi


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
    obi: float
    bid_qty: int
    ask_qty: int
    realized_vol_ticks_1h: float
    spread_ticks: float
    session_tag: str

    @property
    def book_imbalance(self) -> float:
        """Backward-compatible alias for ``obi``."""
        return self.obi


def book_imbalance_l1_only(bid_size: int, ask_size: int) -> float:
    """L1-only OBI; prefer ``OrderBookImbalanceCalculator`` for production."""
    if bid_size < 0 or ask_size < 0:
        raise ValueError("sizes must be non-negative")
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
    """Rolling std-dev of mid price changes expressed in ZN ticks (tick size 1/64)."""

    def __init__(self, window: int, min_std_ticks: float = 0.5) -> None:
        if window < 2:
            raise ValueError(f"window must be >= 2, got {window}")
        if min_std_ticks <= 0:
            raise ValueError(f"min_std_ticks must be positive, got {min_std_ticks}")
        self._window = window
        self._min_std_ticks = min_std_ticks
        self._deltas: list[float] = []

    @property
    def tick_size(self) -> float:
        return TICK_SIZE_FLOAT

    def update(self, mid: float, prev_mid: float | None) -> float:
        if prev_mid is not None and prev_mid > 0:
            delta_ticks = ZN_SEP26.price_delta_to_ticks(mid - prev_mid)
            self._deltas.append(delta_ticks)
            if len(self._deltas) > self._window:
                self._deltas.pop(0)
        if len(self._deltas) < 2:
            return self._min_std_ticks
        mean = sum(self._deltas) / len(self._deltas)
        var = sum((x - mean) ** 2 for x in self._deltas) / (len(self._deltas) - 1)
        return max(sqrt(var), self._min_std_ticks)
