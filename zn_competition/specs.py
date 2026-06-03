"""
CME 10-Year T-Note (ZN) — Sep 2026 competition contract specs.
Verify tick band and fees on TT before live deployment.
"""

from dataclasses import dataclass
from datetime import time
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")
IST = ZoneInfo("Asia/Kolkata")

# Competition window (from rules)
COMPETITION_START_IST = "2026-06-01 03:30:00+05:30"
COMPETITION_END_IST = "2026-06-27 03:30:00+05:30"

WEEKLY_VOLUME_MIN = [200, 300, 400, 500]  # weeks 1–4
TOTAL_VOLUME_MIN = 2000
MAX_POSITION_LOTS = 10
FEE_PER_LOT_USD = 0.50  # per contract traded (one leg); confirm with organizer


@dataclass(frozen=True)
class ZNContract:
    symbol: str = "ZN"
    month_code: str = "U"  # Sep
    year: int = 2026
    tt_instrument: str = "ZN Sep26"  # align to TT Security Search exact name

    # CME Globex ZN standard
    tick_size_points: float = 1 / 64  # half of 1/32 point
    dollars_per_point: float = 1000.0
    dollars_per_tick: float = dollars_per_point * tick_size_points  # 15.625

    # Risk scaffolding (exchange margins move — refresh daily on TT)
    approx_initial_margin_usd: float = 2_500.0
    approx_maintenance_margin_usd: float = 2_300.0

    def ticks_to_dollars(self, ticks: float) -> float:
        return ticks * self.dollars_per_tick

    def dollars_to_ticks(self, dollars: float) -> float:
        return dollars / self.dollars_per_tick


ZN_SEP26 = ZNContract()


# Liquidity windows (CT) — where ZN edge per unit risk is usually highest
HIGH_LIQUIDITY_WINDOWS_CT = [
    (time(7, 20), time(10, 0)),   # US cash + morning
    (time(12, 0), time(15, 0)),  # London afternoon overlap / US midday
    (time(18, 0), time(20, 0)),  # US close / macro headline window
]

# Macro events that dominate ZN — schedule in TT alerts + external calendar
MACRO_EVENT_TAGS = (
    "FOMC_rate_decision",
    "FOMC_minutes",
    "NFP",
    "CPI",
    "PCE",
    "ISM_manufacturing",
    "ISM_services",
    "retail_sales",
    "10Y_auction",
    "30Y_auction",
    "Fed_speakers",
    "jobless_claims",
)
