"""Massive (Polygon.io) REST poller for real market data."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource, normalize_ticker

logger = logging.getLogger(__name__)

MAX_BACKOFF_SECONDS = 120.0


@dataclass(frozen=True, slots=True)
class ParsedQuote:
    ticker: str
    price: float
    timestamp: float  # Unix seconds
    session_open: float | None  # previous close


def to_unix_seconds(ts: float | None) -> float | None:
    """Massive mixes ns / ms timestamps across endpoints; normalize by magnitude."""
    if ts is None or ts <= 0:
        return None
    if ts > 1e17:  # nanoseconds
        return ts / 1e9
    if ts > 1e14:  # microseconds
        return ts / 1e6
    if ts > 1e11:  # milliseconds
        return ts / 1e3
    return float(ts)


def _first_positive(*values: float | None) -> float | None:
    for v in values:
        if v is not None and v > 0:
            return float(v)
    return None


def parse_snapshot(snap) -> ParsedQuote | None:
    """Convert a massive TickerSnapshot into a ParsedQuote, or None if unusable."""
    ticker = getattr(snap, "ticker", None)
    if not ticker:
        return None

    last_trade = snap.last_trade
    minute, day, prev_day = snap.min, snap.day, snap.prev_day

    price = _first_positive(
        last_trade.price if last_trade else None,
        minute.close if minute else None,
        day.close if day else None,
        prev_day.close if prev_day else None,
    )
    if price is None:
        return None

    timestamp = (
        to_unix_seconds(last_trade.sip_timestamp if last_trade else None)
        or to_unix_seconds(snap.updated)
        or time.time()
    )
    session_open = _first_positive(prev_day.close if prev_day else None)
    return ParsedQuote(ticker.upper(), price, timestamp, session_open)


class MassiveDataSource(MarketDataSource):
    """Polls the Massive snapshot endpoint for all tracked tickers in one call.

    Rate limits: free tier = 5 req/min, so the default interval is 15 s.
    Paid tiers: set MASSIVE_POLL_INTERVAL=2..5.
    On failure: exponential backoff (interval * 2^n, capped at 120 s), reset on success.
    """

    def __init__(
        self,
        api_key: str,
        price_cache: PriceCache,
        poll_interval: float = 15.0,
    ) -> None:
        self._api_key = api_key
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None
        self._client: RESTClient | None = None
        self._consecutive_failures = 0

    async def start(self, tickers: list[str]) -> None:
        self._client = RESTClient(api_key=self._api_key)
        self._tickers = list(dict.fromkeys(normalize_ticker(t) for t in tickers))
        await self._poll_once()  # immediate first fill
        self._task = asyncio.create_task(self._poll_loop(), name="massive-poller")
        logger.info(
            "Massive poller started: %d tickers, %.1fs interval",
            len(self._tickers),
            self._interval,
        )

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        self._client = None
        logger.info("Massive poller stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._tickers:
            self._tickers.append(ticker)
            logger.info("Massive: added %s (priced on next poll)", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)
        logger.info("Massive: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internals ---

    def _next_delay(self) -> float:
        if self._consecutive_failures == 0:
            return self._interval
        return min(self._interval * 2**self._consecutive_failures, MAX_BACKOFF_SECONDS)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._next_delay())
            await self._poll_once()

    async def _poll_once(self) -> None:
        if not self._tickers or self._client is None:
            return
        requested = list(self._tickers)  # immutable copy handed to the worker thread
        try:
            snapshots = await asyncio.to_thread(self._fetch_snapshots, requested)
        except Exception as e:  # noqa: BLE001 - BadResponse (401/403/429), urllib3 errors, timeouts
            self._consecutive_failures += 1
            logger.error(
                "Massive poll failed (%d in a row, next try in %.0fs): %s",
                self._consecutive_failures,
                self._next_delay(),
                e,
            )
            return

        self._consecutive_failures = 0
        tracked = set(self._tickers)  # may have changed while we were awaiting
        seen: set[str] = set()
        for snap in snapshots:
            quote = parse_snapshot(snap)
            if quote is None:
                logger.warning("Skipping unusable snapshot for %s", getattr(snap, "ticker", "???"))
                continue
            if quote.ticker not in tracked:
                continue  # removed mid-flight; don't resurrect it in the cache
            self._cache.update(
                quote.ticker,
                quote.price,
                timestamp=quote.timestamp,
                session_open=quote.session_open,
            )
            seen.add(quote.ticker)

        missing = set(requested) - seen
        if missing:
            logger.warning("Massive returned no data for: %s", ", ".join(sorted(missing)))
        logger.debug("Massive poll: %d/%d tickers updated", len(seen), len(requested))

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Blocking HTTP call; always run via asyncio.to_thread."""
        assert self._client is not None
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
