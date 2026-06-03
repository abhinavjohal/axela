"""
Streaming microstructure feature engine for TT quote streams.
Computes session VWAP, vwap_z, book imbalance, and realized vol in ticks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from zn_competition.microstructure import FeatureSnapshot, Quote, RollingStdTicks, book_imbalance
from zn_competition.specs import CT, TICK_SIZE_FLOAT, in_liquidity_window


@dataclass
class SessionVWAP:
    """Session-anchored VWAP; resets on explicit new_session()."""

    _pv: float = 0.0
    _vol: float = 0.0
    _last_mid: float | None = None

    def update(self, quote: Quote) -> float:
        mid = quote.mid
        vol = float(quote.volume) if quote.volume > 0 else 1.0
        self._pv += mid * vol
        self._vol += vol
        self._last_mid = mid
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
        self._last_mid = None


@dataclass
class MicrostructureFeatureEngine:
    vol_window: int = 120
    z_std_floor_ticks: float = 0.5
    _session: SessionVWAP = field(default_factory=SessionVWAP)
    _vol_estimator: RollingStdTicks = field(init=False)
    _prev_mid: float | None = None

    def __post_init__(self) -> None:
        self._vol_estimator = RollingStdTicks(self.vol_window)

    def new_session(self) -> None:
        self._session.reset()
        self._prev_mid = None

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
        mid = quote.mid
        vwap = self._session.update(quote)
        std_ticks = self._vol_estimator.update(mid, self._prev_mid)
        self._prev_mid = mid

        deviation_ticks = (mid - vwap) / TICK_SIZE_FLOAT
        safe_std = max(std_ticks, self.z_std_floor_ticks)
        vwap_z = deviation_ticks / safe_std

        imb = book_imbalance(quote.bid_size, quote.ask_size)
        realized_vol = std_ticks

        return FeatureSnapshot(
            timestamp=quote.timestamp,
            mid=mid,
            vwap=vwap,
            vwap_z=vwap_z,
            book_imbalance=imb,
            realized_vol_ticks_1h=realized_vol,
            spread_ticks=quote.spread_ticks,
            session_tag=self._session_tag(quote.timestamp),
        )
