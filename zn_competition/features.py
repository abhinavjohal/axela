"""
Streaming microstructure feature engine for TT quote streams.

Level 1 only: direct_bid_qty, direct_ask_qty, bid_order_count, ask_order_count.
Price deltas scaled with ZN tick size 1/64 (0.015625) via ``ZN_SEP26``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from zn_competition.microstructure import (
    DirectOBIResult,
    FeatureSnapshot,
    OrderBookImbalanceCalculator,
    Quote,
    RollingStdTicks,
    order_book_from_quote,
)
from zn_competition.specs import CT, get_instrument_spec, in_liquidity_window

FEATURE_TABLE_COLUMNS: tuple[str, ...] = (
    "timestamp",
    "direct_obi",
    "avg_bid_order_size",
    "avg_ask_order_size",
)


@dataclass
class OBIHistoryBuffer:
    """Fixed-length rolling store of direct OBI values."""

    max_length: int = 500

    _values: list[float] = field(default_factory=list)

    def __post_init__(self) -> None:
        if self.max_length < 1:
            raise ValueError(f"max_length must be >= 1, got {self.max_length}")

    def append(self, direct_obi: float) -> None:
        self._values.append(float(direct_obi))
        if len(self._values) > self.max_length:
            self._values.pop(0)

    def reset(self) -> None:
        self._values.clear()

    @property
    def values(self) -> tuple[float, ...]:
        return tuple(self._values)

    def __len__(self) -> int:
        return len(self._values)


@dataclass
class SessionVWAP:
    """Session-anchored VWAP; resets on explicit ``new_session()``."""

    _pv: float = 0.0
    _vol: float = 0.0

    def update(self, quote: Quote) -> float:
        mid = quote.mid
        vol = float(quote.volume) if quote.volume > 0 else 1.0
        self._pv += mid * vol
        self._vol += vol
        if self._vol <= 0:
            raise RuntimeError("session VWAP volume must be positive")
        return self._pv / self._vol

    @property
    def value(self) -> float | None:
        if self._vol <= 0:
            return None
        return self._pv / self._vol

    def reset(self) -> None:
        self._pv = 0.0
        self._vol = 0.0


@dataclass
class MicrostructureFeatureEngine:
    """
    Main feature pipeline: quote → ``FeatureSnapshot`` + direct OBI history.

    All price deltas converted to ticks via ``ZN_SEP26.price_delta_to_ticks``
    (minimum increment = 1/64 = 0.015625).
    """

    vol_window: int = 120
    z_std_floor_ticks: float = 0.5
    obi_history_length: int = 500
    instrument_id: str = "ZN"

    _session: SessionVWAP = field(default_factory=SessionVWAP)
    _vol_estimator: RollingStdTicks = field(init=False)
    _obi_history: OBIHistoryBuffer = field(init=False)
    _prev_mid: float | None = None
    _snapshots: list[FeatureSnapshot] = field(default_factory=list)

    def __post_init__(self) -> None:
        self._vol_estimator = RollingStdTicks(
            self.vol_window,
            min_std_ticks=self.z_std_floor_ticks,
            instrument_id=self.instrument_id,
        )
        self._obi_history = OBIHistoryBuffer(max_length=self.obi_history_length)

    @property
    def obi_history(self) -> tuple[float, ...]:
        """Rolling array of direct OBI ratios, one entry per processed quote."""
        return self._obi_history.values

    @property
    def snapshots(self) -> tuple[FeatureSnapshot, ...]:
        """All feature snapshots emitted by this engine instance."""
        return tuple(self._snapshots)

    def new_session(self) -> None:
        self._session.reset()
        self._obi_history.reset()
        self._snapshots.clear()
        self._prev_mid = None

    def compute_direct_obi(self, quote: Quote) -> DirectOBIResult:
        return OrderBookImbalanceCalculator.from_quote(quote)

    def _session_tag(self, timestamp: str) -> str:
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        except ValueError:
            return "unknown"
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=CT)
        ct_time = dt.astimezone(CT).time()
        if in_liquidity_window(ct_time):
            return "high_liquidity"
        return "off_peak"

    def update(self, quote: Quote) -> FeatureSnapshot:
        """Process one quote; append direct OBI to history."""
        mid = quote.mid
        vwap = self._session.update(quote)
        std_ticks = self._vol_estimator.update(mid, self._prev_mid)
        self._prev_mid = mid

        spec = get_instrument_spec(self.instrument_id)
        deviation_ticks = spec.price_delta_to_ticks(mid - vwap)
        safe_std = max(std_ticks, self.z_std_floor_ticks)
        vwap_z = deviation_ticks / safe_std

        obi_result = self.compute_direct_obi(quote)
        self._obi_history.append(obi_result.direct_obi)

        spread_ticks = order_book_from_quote(quote).spread_ticks

        snapshot = FeatureSnapshot(
            timestamp=quote.timestamp,
            mid=mid,
            vwap=vwap,
            vwap_z=vwap_z,
            direct_obi=obi_result.direct_obi,
            avg_bid_order_size=obi_result.avg_bid_order_size,
            avg_ask_order_size=obi_result.avg_ask_order_size,
            direct_bid_qty=obi_result.direct_bid_qty,
            direct_ask_qty=obi_result.direct_ask_qty,
            bid_order_count=obi_result.bid_order_count,
            ask_order_count=obi_result.ask_order_count,
            realized_vol_ticks_1h=std_ticks,
            spread_ticks=spread_ticks,
            session_tag=self._session_tag(quote.timestamp),
        )
        self._snapshots.append(snapshot)
        return snapshot

    def feature_table(self) -> tuple[dict[str, float | str], ...]:
        """
        Clean per-tick table with Level 1 OBI features (column order fixed).

        Columns: ``timestamp``, ``direct_obi``, ``avg_bid_order_size``,
        ``avg_ask_order_size``.
        """
        rows: list[dict[str, float | str]] = []
        for snap in self._snapshots:
            rows.append(
                {
                    "timestamp": snap.timestamp,
                    "direct_obi": snap.direct_obi,
                    "avg_bid_order_size": snap.avg_bid_order_size,
                    "avg_ask_order_size": snap.avg_ask_order_size,
                }
            )
        return tuple(rows)

    def process_quotes(self, quotes: list[Quote]) -> list[FeatureSnapshot]:
        """Batch-process a quote stream; returns aligned feature snapshot array."""
        if not quotes:
            raise ValueError("quotes list is empty")
        return [self.update(quote) for quote in quotes]
