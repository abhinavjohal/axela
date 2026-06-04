"""Shared Level 1 mock market rows for unit tests (no multi-depth book)."""

from __future__ import annotations

from zn_competition.microstructure import (
    Level1MarketRow,
    OrderBookSnapshot,
    Quote,
    book_from_level1,
    quote_from_level1,
)
from zn_competition.specs import TICK_SIZE_FLOAT

# Balanced inside market — churn / feature pipeline defaults
BALANCED_L1 = Level1MarketRow(
    direct_bid_price=112.0,
    direct_ask_price=112.03125,
    direct_bid_qty=20,
    direct_ask_qty=20,
    bid_order_count=4,
    ask_order_count=4,
)

# Strong bid-side OBI > 0.7 for HFT long entry tests
BULLISH_L1 = Level1MarketRow(
    direct_bid_price=112.0,
    direct_ask_price=112.03125,
    direct_bid_qty=110,
    direct_ask_qty=10,
    bid_order_count=20,
    ask_order_count=5,
)

# Strong ask-side OBI for scratch / flip tests
BEARISH_L1 = Level1MarketRow(
    direct_bid_price=112.0,
    direct_ask_price=112.03125,
    direct_bid_qty=10,
    direct_ask_qty=110,
    bid_order_count=5,
    ask_order_count=20,
    timestamp="2026-06-03T14:00:01+00:00",
)

# One-tick spread for tick-scaling tests
ONE_TICK_SPREAD_L1 = Level1MarketRow(
    direct_bid_price=112.0,
    direct_ask_price=112.0 + TICK_SIZE_FLOAT,
    direct_bid_qty=10,
    direct_ask_qty=10,
    bid_order_count=2,
    ask_order_count=2,
    timestamp="t",
)

# Feature pipeline history stream
FEATURE_STREAM_L1 = Level1MarketRow(
    direct_bid_price=112.0,
    direct_ask_price=112.03125,
    direct_bid_qty=70,
    direct_ask_qty=10,
    bid_order_count=7,
    ask_order_count=2,
)

# OBI table row
OBI_TABLE_L1 = Level1MarketRow(
    direct_bid_price=112.0,
    direct_ask_price=112.03125,
    direct_bid_qty=100,
    direct_ask_qty=50,
    bid_order_count=10,
    ask_order_count=5,
    timestamp="t",
)


def l1_quote(row: Level1MarketRow, *, timestamp: str | None = None) -> Quote:
    if timestamp is None:
        return quote_from_level1(row)
    return quote_from_level1(
        Level1MarketRow(
            direct_bid_price=row.direct_bid_price,
            direct_ask_price=row.direct_ask_price,
            direct_bid_qty=row.direct_bid_qty,
            direct_ask_qty=row.direct_ask_qty,
            bid_order_count=row.bid_order_count,
            ask_order_count=row.ask_order_count,
            timestamp=timestamp,
            volume=row.volume,
        )
    )


def l1_book(row: Level1MarketRow, *, timestamp: str | None = None) -> OrderBookSnapshot:
    return book_from_level1(
        Level1MarketRow(
            direct_bid_price=row.direct_bid_price,
            direct_ask_price=row.direct_ask_price,
            direct_bid_qty=row.direct_bid_qty,
            direct_ask_qty=row.direct_ask_qty,
            bid_order_count=row.bid_order_count,
            ask_order_count=row.ask_order_count,
            timestamp=timestamp or row.timestamp,
            volume=row.volume,
        )
    )
