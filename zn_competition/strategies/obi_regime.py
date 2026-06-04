"""
Dual-regime OBI session clock — Sniper (0.85) vs Volume Churner (0.65).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")

SNIPER_THRESHOLD = 0.85
VOLUME_THRESHOLD = 0.65
SNIPER_START_ET = (8, 30)
SNIPER_END_ET = (11, 30)
VOLUME_START_ET = (12, 0)
VOLUME_END_ET = (14, 0)


class OBIRegimeMode(str, Enum):
    SNIPER_MODE = "SNIPER_MODE"
    VOLUME_MODE = "VOLUME_MODE"
    OFF = "OFF"


@dataclass(frozen=True)
class OBIRegimeSnapshot:
    mode: OBIRegimeMode
    entry_threshold: float
    flip_threshold: float
    short_entry_threshold: float

    @property
    def allows_new_entries(self) -> bool:
        return self.mode != OBIRegimeMode.OFF and self.entry_threshold > 0.0


def _minutes_et(hour: int, minute: int) -> int:
    return hour * 60 + minute


def parse_timestamp_utc(timestamp: str) -> datetime:
    text = timestamp.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


@dataclass
class DualRegimeSessionClock:
    """
    SNIPER_MODE (0.85): 08:30–11:30 ET — equity open / peak macro.
    VOLUME_MODE (0.65): 12:00–14:00 ET — midday consolidation churn.
    OFF: all other times.
    """

    sniper_threshold: float = SNIPER_THRESHOLD
    volume_threshold: float = VOLUME_THRESHOLD
    sniper_start: tuple[int, int] = SNIPER_START_ET
    sniper_end: tuple[int, int] = SNIPER_END_ET
    volume_start: tuple[int, int] = VOLUME_START_ET
    volume_end: tuple[int, int] = VOLUME_END_ET
    _last_mode: OBIRegimeMode = OBIRegimeMode.OFF

    def evaluate(self, timestamp: str) -> OBIRegimeSnapshot:
        dt_et = parse_timestamp_utc(timestamp).astimezone(ET)
        mins = _minutes_et(dt_et.hour, dt_et.minute)
        sniper_lo = _minutes_et(*self.sniper_start)
        sniper_hi = _minutes_et(*self.sniper_end)
        volume_lo = _minutes_et(*self.volume_start)
        volume_hi = _minutes_et(*self.volume_end)

        if sniper_lo <= mins <= sniper_hi:
            return self._snapshot(OBIRegimeMode.SNIPER_MODE, self.sniper_threshold)
        if volume_lo <= mins <= volume_hi:
            return self._snapshot(OBIRegimeMode.VOLUME_MODE, self.volume_threshold)
        return self._snapshot(OBIRegimeMode.OFF, 0.0)

    def _snapshot(self, mode: OBIRegimeMode, entry: float) -> OBIRegimeSnapshot:
        return OBIRegimeSnapshot(
            mode=mode,
            entry_threshold=entry,
            flip_threshold=-entry,
            short_entry_threshold=-entry,
        )

    def regime_changed(self, snapshot: OBIRegimeSnapshot) -> bool:
        changed = snapshot.mode != self._last_mode
        self._last_mode = snapshot.mode
        return changed

    def reset(self) -> None:
        self._last_mode = OBIRegimeMode.OFF
