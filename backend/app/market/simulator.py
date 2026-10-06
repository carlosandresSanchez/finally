"""GBM market simulator and its MarketDataSource wrapper."""

import asyncio
import contextlib
import math
import random

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource
from .seed_prices import (
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    FINANCE_TICKERS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TECH_TICKERS,
    TICKER_PARAMS,
    TSLA_CORR,
)

# 500ms expressed as a fraction of a trading year (252 days * 6.5h)
DEFAULT_DT = 0.5 / (252 * 6.5 * 3600)


class GBMSimulator:
    """Generates correlated geometric Brownian motion price paths."""

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
    ) -> None:
        self._dt = dt
        self._event_prob = event_probability
        self._prices: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._tickers: list[str] = []
        self._cholesky: np.ndarray | None = None
        for ticker in tickers:
            self._add(ticker)
        self._rebuild_cholesky()

    def _add(self, ticker: str) -> bool:
        if ticker in self._prices:
            return False
        self._tickers.append(ticker)
        self._prices[ticker] = SEED_PRICES.get(ticker, random.uniform(50, 300))
        self._params[ticker] = TICKER_PARAMS.get(ticker, DEFAULT_PARAMS)
        return True

    def add_ticker(self, ticker: str) -> None:
        if self._add(ticker):
            self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker]
        del self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    def step(self) -> dict[str, float]:
        """Advance one time step. Returns {ticker: new_price}."""
        n = len(self._tickers)
        if n == 0:
            return {}
        z = np.random.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]
            drift = (mu - 0.5 * sigma**2) * self._dt
            diffusion = sigma * math.sqrt(self._dt) * z[i]
            self._prices[ticker] *= math.exp(drift + diffusion)

            if random.random() < self._event_prob:
                shock = random.uniform(0.02, 0.05) * random.choice([-1, 1])
                self._prices[ticker] *= 1 + shock

            result[ticker] = round(self._prices[ticker], 2)
        return result

    def _rebuild_cholesky(self) -> None:
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self.correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def correlation(t1: str, t2: str) -> float:
        if t1 in TECH_TICKERS and t2 in TECH_TICKERS:
            return INTRA_TECH_CORR
        if t1 in FINANCE_TICKERS and t2 in FINANCE_TICKERS:
            return INTRA_FINANCE_CORR
        if t1 == "TSLA" or t2 == "TSLA":
            return TSLA_CORR
        return CROSS_GROUP_CORR


class SimulatorDataSource(MarketDataSource):
    """Runs a GBMSimulator in a background task, writing to the PriceCache."""

    def __init__(self, price_cache: PriceCache, update_interval: float = 0.5) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        if self._task is not None:
            return
        self._sim = GBMSimulator(tickers=list(tickers))
        # Seed the cache immediately so consumers have prices before the first tick
        for ticker in self._sim.get_tickers():
            price = self._sim.get_price(ticker)
            if price is not None:
                self._cache.update(ticker, round(price, 2))
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    async def add_ticker(self, ticker: str) -> None:
        if self._sim is None:
            return
        self._sim.add_ticker(ticker)
        price = self._sim.get_price(ticker)
        if price is not None and ticker not in self._cache:
            self._cache.update(ticker, round(price, 2))

    async def remove_ticker(self, ticker: str) -> None:
        if self._sim is None:
            return
        self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    async def _run_loop(self) -> None:
        while True:
            if self._sim is not None:
                for ticker, price in self._sim.step().items():
                    self._cache.update(ticker, price)
            await asyncio.sleep(self._interval)
