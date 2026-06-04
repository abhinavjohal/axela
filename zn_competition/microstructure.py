"""
ZN market microstructure utilities — tick grid, spread, Level 1 direct OBI.
Standard library only. All tick math uses ``specs.TICK_SIZE_FLOAT`` (0.015625).
"""

from __future__ import annotations

from dataclasses import dataclass
from math import sqrt

from zn_competition.specs import TICK_SIZE_FLOAT, ZN_SEP26

# TT ADL Level 1 field names (inside market direct quantities + order counts).
LEVEL1_PRICE_FIELDS = frozenset({"direct_bid_price", "direct_ask_price"})
LEVEL1_QTY_FIELDS = frozenset({"direct_bid_qty", "direct_ask_qty"})
LEVEL1_COUNT_FIELDS = frozenset({"bid_order_count", "ask_order_count"})
LEVEL1_LEGACY_QTY_ALIASES = frozenset({"bid_size", "ask_size"})
LEVEL1_LEGACY_PRICE_ALIASES = frozenset({"bid", "ask"})


@dataclass(frozen=True)
class Level1MarketRow:
    """
    TT ADL Level 1 inside-market columns (no multi-depth book).

    Columns: ``direct_bid_price``, ``direct_ask_price``, ``direct_bid_qty``,
    ``direct_ask_qty``, ``bid_order_count``, ``ask_order_count``.
    """

    direct_bid_price: float
    direct_ask_price: float
    direct_bid_qty: int
    direct_ask_qty: int
    bid_order_count: int
    ask_order_count: int
    timestamp: str = "2026-06-03T14:00:00+00:00"
    volume: int = 1

    def __post_init__(self) -> None:
        if self.direct_bid_price <= 0 or self.direct_ask_price <= 0:
            raise ValueError("direct bid/ask prices must be positive")
        if self.direct_ask_price < self.direct_bid_price:
            raise ValueError("direct_ask_price must be >= direct_bid_price")
        if self.direct_bid_qty < 0 or self.direct_ask_qty < 0:
            raise ValueError("direct quantities must be non-negative")
        if self.bid_order_count < 0 or self.ask_order_count < 0:
            raise ValueError("order counts must be non-negative")


@dataclass(frozen=True)
class Quote:
    timestamp: str
    bid: float
    ask: float
    direct_bid_qty: int = 0
    direct_ask_qty: int = 0
    bid_order_count: int = 1
    ask_order_count: int = 1
    last: float | None = None
    volume: int = 0

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError(f"bid/ask must be positive: bid={self.bid}, ask={self.ask}")
        if self.ask < self.bid:
            raise ValueError(f"crossed market: bid={self.bid}, ask={self.ask}")
        if self.direct_bid_qty < 0 or self.direct_ask_qty < 0:
            raise ValueError("direct_bid_qty and direct_ask_qty must be non-negative")
        if self.bid_order_count < 0 or self.ask_order_count < 0:
            raise ValueError("bid_order_count and ask_order_count must be non-negative")

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

    @property
    def direct_bid_price(self) -> float:
        return self.bid

    @property
    def direct_ask_price(self) -> float:
        return self.ask


@dataclass(frozen=True)
class OrderBookSnapshot:
    """Level 1 inside-market snapshot (TT ADL direct qty + order counts)."""

    timestamp: str
    bid: float
    ask: float
    direct_bid_qty: int
    direct_ask_qty: int
    bid_order_count: int
    ask_order_count: int

    def __post_init__(self) -> None:
        if self.bid <= 0 or self.ask <= 0:
            raise ValueError(f"bid/ask must be positive: bid={self.bid}, ask={self.ask}")
        if self.ask < self.bid:
            raise ValueError(f"crossed market: bid={self.bid}, ask={self.ask}")
        for name, val in (
            ("direct_bid_qty", self.direct_bid_qty),
            ("direct_ask_qty", self.direct_ask_qty),
            ("bid_order_count", self.bid_order_count),
            ("ask_order_count", self.ask_order_count),
        ):
            if val < 0:
                raise ValueError(f"{name} must be non-negative, got {val}")

    @property
    def inside_bid(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.bid)

    @property
    def inside_ask(self) -> float:
        return ZN_SEP26.round_price_to_tick(self.ask)

    @property
    def spread_ticks(self) -> float:
        return ZN_SEP26.price_delta_to_ticks(self.ask - self.bid)

    @property
    def direct_bid_price(self) -> float:
        return self.bid

    @property
    def direct_ask_price(self) -> float:
        return self.ask


def quote_from_level1(row: Level1MarketRow) -> Quote:
    """Build a ``Quote`` from explicit Level 1 ADL columns."""
    return Quote(
        timestamp=row.timestamp,
        bid=ZN_SEP26.round_price_to_tick(row.direct_bid_price),
        ask=ZN_SEP26.round_price_to_tick(row.direct_ask_price),
        direct_bid_qty=row.direct_bid_qty,
        direct_ask_qty=row.direct_ask_qty,
        bid_order_count=row.bid_order_count,
        ask_order_count=row.ask_order_count,
        volume=row.volume,
    )


def book_from_level1(row: Level1MarketRow) -> OrderBookSnapshot:
    """Build an ``OrderBookSnapshot`` from explicit Level 1 ADL columns."""
    return order_book_from_quote(quote_from_level1(row))


def _avg_order_size(qty: int, order_count: int) -> float:
    if order_count <= 0:
        return 0.0
    return qty / order_count


def calculate_direct_obi(direct_bid_qty: int, direct_ask_qty: int) -> float:
    """
    TT ADL / Python direct OBI::

        direct_obi = (direct_bid_qty - direct_ask_qty)
                     / (direct_bid_qty + direct_ask_qty)

    Returns 0.0 when total displayed quantity is zero.
    """
    if direct_bid_qty < 0 or direct_ask_qty < 0:
        raise ValueError("quantities must be non-negative")
    total = direct_bid_qty + direct_ask_qty
    if total <= 0:
        return 0.0
    return (direct_bid_qty - direct_ask_qty) / total


@dataclass(frozen=True)
class DirectOBIResult:
    """Level 1 OBI and average order sizes from TT direct fields."""

    direct_obi: float
    direct_bid_qty: int
    direct_ask_qty: int
    bid_order_count: int
    ask_order_count: int
    avg_bid_order_size: float
    avg_ask_order_size: float

    @property
    def obi(self) -> float:
        """Backward-compatible alias for ``direct_obi``."""
        return self.direct_obi


def parse_level1_book_fields(
    direct_bid_qty: int,
    direct_ask_qty: int,
    bid_order_count: int,
    ask_order_count: int,
) -> DirectOBIResult:
    """
    Validate TT Level 1 inputs and compute ``direct_obi`` plus average order sizes.

    ``avg_bid_order_size = direct_bid_qty / bid_order_count`` (0 when count is 0).
    ``avg_ask_order_size = direct_ask_qty / ask_order_count`` (0 when count is 0).
    """
    for name, val in (
        ("direct_bid_qty", direct_bid_qty),
        ("direct_ask_qty", direct_ask_qty),
        ("bid_order_count", bid_order_count),
        ("ask_order_count", ask_order_count),
    ):
        if val < 0:
            raise ValueError(f"{name} must be non-negative, got {val}")

    return DirectOBIResult(
        direct_obi=calculate_direct_obi(direct_bid_qty, direct_ask_qty),
        direct_bid_qty=direct_bid_qty,
        direct_ask_qty=direct_ask_qty,
        bid_order_count=bid_order_count,
        ask_order_count=ask_order_count,
        avg_bid_order_size=_avg_order_size(direct_bid_qty, bid_order_count),
        avg_ask_order_size=_avg_order_size(direct_ask_qty, ask_order_count),
    )


def parse_level1_from_mapping(
    row: dict[str, str],
    *,
    default_direct_bid_qty: int = 0,
    default_direct_ask_qty: int = 0,
) -> DirectOBIResult:
    """
    Parse Level 1 quantities from a CSV/TT row dict.

    Accepts canonical names ``direct_bid_qty`` / ``direct_ask_qty`` or legacy
    ``bid_size`` / ``ask_size``. Does not read or aggregate multi-level depth.
    """

    def _int(key: str, default: int = 0) -> int:
        raw = row.get(key, "").strip()
        if not raw:
            return default
        return int(float(raw))

    bid_raw = row.get("direct_bid_qty", "").strip() or row.get("bid_size", "").strip()
    ask_raw = row.get("direct_ask_qty", "").strip() or row.get("ask_size", "").strip()
    direct_bid_qty = int(float(bid_raw)) if bid_raw else default_direct_bid_qty
    direct_ask_qty = int(float(ask_raw)) if ask_raw else default_direct_ask_qty

    bid_order_count = _int("bid_order_count", default=1 if direct_bid_qty > 0 else 0)
    ask_order_count = _int("ask_order_count", default=1 if direct_ask_qty > 0 else 0)

    return parse_level1_book_fields(
        direct_bid_qty,
        direct_ask_qty,
        bid_order_count,
        ask_order_count,
    )


class OrderBookImbalanceCalculator:
    """
    Level 1 direct OBI calculator (TT ADL parity).

    Formula::

        direct_obi = (direct_bid_qty - direct_ask_qty)
                     / (direct_bid_qty + direct_ask_qty)
    """

    @staticmethod
    def from_quantities(
        direct_bid_qty: int,
        direct_ask_qty: int,
        bid_order_count: int = 1,
        ask_order_count: int = 1,
    ) -> DirectOBIResult:
        return parse_level1_book_fields(
            direct_bid_qty,
            direct_ask_qty,
            bid_order_count,
            ask_order_count,
        )

    @classmethod
    def from_snapshot(cls, book: OrderBookSnapshot) -> DirectOBIResult:
        return cls.from_quantities(
            book.direct_bid_qty,
            book.direct_ask_qty,
            book.bid_order_count,
            book.ask_order_count,
        )

    @classmethod
    def from_quote(cls, quote: Quote) -> DirectOBIResult:
        return cls.from_quantities(
            quote.direct_bid_qty,
            quote.direct_ask_qty,
            quote.bid_order_count,
            quote.ask_order_count,
        )


def calculate_order_book_imbalance(book: OrderBookSnapshot) -> float:
    """Return ``direct_obi`` for an ``OrderBookSnapshot``."""
    return OrderBookImbalanceCalculator.from_snapshot(book).direct_obi


def order_book_from_quote(quote: Quote) -> OrderBookSnapshot:
    return OrderBookSnapshot(
        timestamp=quote.timestamp,
        bid=quote.bid,
        ask=quote.ask,
        direct_bid_qty=quote.direct_bid_qty,
        direct_ask_qty=quote.direct_ask_qty,
        bid_order_count=quote.bid_order_count,
        ask_order_count=quote.ask_order_count,
    )


@dataclass(frozen=True)
class FeatureSnapshot:
    timestamp: str
    mid: float
    vwap: float
    vwap_z: float
    direct_obi: float
    avg_bid_order_size: float
    avg_ask_order_size: float
    direct_bid_qty: int
    direct_ask_qty: int
    bid_order_count: int
    ask_order_count: int
    realized_vol_ticks_1h: float
    spread_ticks: float
    session_tag: str

    @property
    def obi(self) -> float:
        """Backward-compatible alias for ``direct_obi``."""
        return self.direct_obi

    @property
    def book_imbalance(self) -> float:
        """Backward-compatible alias for ``direct_obi``."""
        return self.direct_obi


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
