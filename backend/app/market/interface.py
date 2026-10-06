"""Abstract interface for market data sources."""

from __future__ import annotations

import re
from abc import ABC, abstractmethod

_TICKER_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")


def normalize_ticker(raw: str) -> str:
    """Canonical ticker form used everywhere (DB, cache, sources, SSE).

    ' aapl ' -> 'AAPL'; 'brk.b' -> 'BRK.B'. Raises ValueError for garbage
    such as '', '123', 'AAPL;DROP', or anything longer than 10 chars.
    """
    ticker = raw.strip().upper()
    if not _TICKER_RE.fullmatch(ticker):
        raise ValueError(f"Invalid ticker symbol: {raw!r}")
    return ticker


class MarketDataSource(ABC):
    """Contract for market data providers.

    Implementations push PriceUpdates into a shared PriceCache from a background
    asyncio task. Consumers never call the source for prices; they read the cache.

    Obligations for every implementation:
      * Tickers passed in are normalized via normalize_ticker() (idempotent).
      * start(): seed the cache as soon as possible (simulator: synchronously;
        Massive: one immediate poll) so the first SSE event is not empty.
      * The background task must never die on a bad tick / failed request:
        log and continue.
      * remove_ticker() also removes the ticker from the cache.
      * stop() is idempotent and guarantees no further cache writes.

    Lifecycle:
        source = create_market_data_source(cache)
        await source.start(["AAPL", "GOOGL"])
        await source.add_ticker("TSLA")
        await source.remove_ticker("GOOGL")
        await source.stop()
    """

    @abstractmethod
    async def start(self, tickers: list[str]) -> None:
        """Begin producing updates for `tickers`. Call exactly once."""

    @abstractmethod
    async def stop(self) -> None:
        """Cancel the background task and release resources. Idempotent."""

    @abstractmethod
    async def add_ticker(self, ticker: str) -> None:
        """Start tracking `ticker`. No-op if already tracked."""

    @abstractmethod
    async def remove_ticker(self, ticker: str) -> None:
        """Stop tracking `ticker` and drop it from the cache. No-op if absent."""

    @abstractmethod
    def get_tickers(self) -> list[str]:
        """Currently tracked tickers (a copy)."""
