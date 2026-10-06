"""Massive (formerly Polygon.io) REST poller implementing MarketDataSource."""

import asyncio
import contextlib
import logging

from massive import RESTClient
from massive.rest.models import SnapshotMarketType

from .cache import PriceCache
from .interface import MarketDataSource

logger = logging.getLogger(__name__)


class MassiveDataSource(MarketDataSource):
    """Polls the snapshot endpoint (one call for all tickers) on an interval."""

    def __init__(self, api_key: str, price_cache: PriceCache, poll_interval: float = 15.0) -> None:
        self._client = RESTClient(api_key=api_key)
        self._cache = price_cache
        self._interval = poll_interval
        self._tickers: list[str] = []
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None:
            return
        self._tickers = list(dict.fromkeys(tickers))
        await self._poll_once()  # populate the cache right away
        self._task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        if ticker not in self._tickers:
            self._tickers.append(ticker)

    async def remove_ticker(self, ticker: str) -> None:
        self._tickers = [t for t in self._tickers if t != ticker]
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    async def _poll_loop(self) -> None:
        while True:
            await asyncio.sleep(self._interval)
            await self._poll_once()

    async def _poll_once(self) -> None:
        """Fetch one snapshot batch. Errors are logged; the loop keeps running."""
        if not self._tickers:
            return
        tickers = list(self._tickers)
        try:
            snapshots = await asyncio.to_thread(self._fetch, tickers)
        except Exception as exc:  # 401/403/429/5xx/network: retry next interval
            logger.warning("Massive poll failed: %s", exc)
            return
        for snap in snapshots:
            try:
                ticker = snap.ticker
                if ticker not in self._tickers:
                    continue  # removed while the request was in flight
                self._cache.update(
                    ticker=ticker,
                    price=float(snap.last_trade.price),
                    timestamp=snap.last_trade.timestamp / 1000.0,  # ms -> s
                )
            except (AttributeError, TypeError, ValueError) as exc:
                logger.warning("Skipping malformed snapshot %r: %s", snap, exc)

    def _fetch(self, tickers: list[str]) -> list:
        return list(
            self._client.get_snapshot_all(
                market_type=SnapshotMarketType.STOCKS,
                tickers=tickers,
            )
        )
