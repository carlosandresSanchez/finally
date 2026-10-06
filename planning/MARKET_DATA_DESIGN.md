# Market Data Backend — Detailed Design (v2)

Implementation-ready design for the FinAlly market data subsystem: the unified
`MarketDataSource` API, the in-memory `PriceCache`, the GBM simulator, the
Massive (Polygon.io) REST client, the SSE stream, and how the rest of the
backend plugs into them.

This document **supersedes `planning/archive/MARKET_DATA_DESIGN.md`**. It keeps
the architecture that was built and tested (`backend/app/market/`, 73 passing
tests), and specifies the changes needed to make it correct against the real
Massive SDK and complete with respect to `PLAN.md`. Every code block below is
meant to be dropped into the named file as-is.

---

## Table of Contents

1. [Goals & Requirements](#1-goals--requirements)
2. [Current State & Gaps Found](#2-current-state--gaps-found)
3. [Architecture](#3-architecture)
4. [File Layout](#4-file-layout)
5. [Data Model — `models.py`](#5-data-model--modelspy)
6. [Price Cache — `cache.py`](#6-price-cache--cachepy)
7. [Unified API — `interface.py`](#7-unified-api--interfacepy)
8. [Seed Data — `seed_prices.py`](#8-seed-data--seed_pricespy)
9. [GBM Simulator — `simulator.py`](#9-gbm-simulator--simulatorpy)
10. [Massive API Client — `massive_client.py`](#10-massive-api-client--massive_clientpy)
11. [Factory & Configuration — `factory.py`](#11-factory--configuration--factorypy)
12. [SSE Streaming — `stream.py`](#12-sse-streaming--streampy)
13. [FastAPI Integration](#13-fastapi-integration)
14. [Frontend Contract](#14-frontend-contract)
15. [Testing](#15-testing)
16. [Edge Cases & Failure Modes](#16-edge-cases--failure-modes)
17. [Implementation Checklist](#17-implementation-checklist)

---

## 1. Goals & Requirements

Taken from `PLAN.md` §6, §8 and §10:

| # | Requirement | Where it lives |
|---|---|---|
| R1 | Two price sources (simulator, Massive) behind **one interface**; downstream code is source-agnostic | `interface.py`, `factory.py` |
| R2 | Source picked by env: `MASSIVE_API_KEY` non-empty → Massive, else simulator | `factory.py` |
| R3 | Simulator: GBM, ~500 ms ticks, correlated sectors, random 2–5 % "events", realistic seeds | `simulator.py`, `seed_prices.py` |
| R4 | Massive: REST polling (not WebSocket), one call for all tickers, 15 s on free tier, 2–15 s on paid | `massive_client.py` |
| R5 | Shared in-memory cache holding latest price, previous price, timestamp per ticker | `cache.py` |
| R6 | `GET /api/stream/prices` SSE, ~500 ms cadence, each event carries ticker, price, previous price, timestamp, direction | `stream.py` |
| R7 | Watchlist shows **daily change %** | `PriceUpdate.day_change_percent` (new) |
| R8 | Dynamic tickers: watchlist add/remove (REST or LLM) starts/stops tracking | `add_ticker` / `remove_ticker` |
| R9 | Trades and portfolio valuation read the current price | `PriceCache.get_price()` |

Non-goals: order books, historical bars, multi-user fan-out, and WebSockets.

---

## 2. Current State & Gaps Found

The v1 code works with the simulator and all 73 tests pass. A review of the
code against the actual `massive` SDK (installed version in `uv.lock`) and
against `PLAN.md` turned up the following issues. This design fixes each one.

| # | Severity | Issue | Fix (section) |
|---|---|---|---|
| G1 | **Critical** | `massive_client.py` reads `snap.last_trade.timestamp`, but the SDK's `LastTrade` model has **no `timestamp` attribute**. Its fields are `sip_timestamp`, `participant_timestamp` and `trf_timestamp`, and they are in **nanoseconds**. Every snapshot raises `AttributeError`, gets skipped, and **no price ever reaches the cache with a real API key**. The tests pass only because they use `MagicMock`, which accepts any attribute name. Verified by feeding a real `TickerSnapshot.from_dict(...)` through `_poll_once()`: the cache stays empty. | §10.3, §15.4 |
| G2 | High | `PLAN.md` requires a **daily change %** in the watchlist. `PriceUpdate` only knows the tick-to-tick change (`previous_price`), which is ~0.00 % every 500 ms. | §5, §6 |
| G3 | Medium | `stream.py` registers the route on a **module-level** `APIRouter`. Calling `create_stream_router()` twice (tests, app reload) registers `/prices` twice. | §12 |
| G4 | Medium | The simulator does not normalize tickers (`"aapl"` and `"AAPL"` become two series), and nothing validates ticker format. | §7.2, §9.2 |
| G5 | Medium | The Massive poller keeps hammering at a fixed interval during outages and 429 rate limits. | §10.4 |
| G6 | Low | The Massive poll interval is hard-coded (15 s) and can't be changed for paid tiers without a code change. | §11 |
| G7 | Low | `PriceCache.version` is read without the lock, and `timestamp or time.time()` treats a `0` timestamp as missing. | §6 |
| G8 | Low | The simulator uses global `random` / `np.random` state, so tests cannot be deterministic. | §9.1 |
| G9 | Low | SSE sends nothing while prices are unchanged (Massive between polls, or market closed). Some proxies close idle streams. | §12 |
| G10 | Low | `planning/archive/MASSIVE_API.md` documents `day.previous_close` and `last_trade.timestamp`, but neither exists on the SDK models. The correct fields are `prev_day.close`, `todays_change_percent` and `last_trade.sip_timestamp`. | §10.2 |

All changes are **backwards compatible** for consumers: `PriceUpdate.to_dict()`
only gains keys, and every existing method keeps its signature (new parameters
are optional keywords).

---

## 3. Architecture

```
                 ┌──────────────────────────── create_market_data_source(cache) ──┐
                 │  MASSIVE_API_KEY set?                                           │
                 ▼ no                                          yes ▼                 │
   ┌───────────────────────────┐                 ┌────────────────────────────┐    │
   │ SimulatorDataSource       │                 │ MassiveDataSource          │    │
   │  └─ GBMSimulator (numpy)  │                 │  └─ massive.RESTClient     │    │
   │  asyncio task, every 0.5s │                 │  asyncio task, every 15s   │    │
   └─────────────┬─────────────┘                 │  (sync HTTP in to_thread)  │    │
                 │ cache.update(...)             └──────────────┬─────────────┘    │
                 └──────────────────┐        ┌──────────────────┘                  │
                                    ▼        ▼                                     │
                         ┌────────────────────────────────┐                        │
                         │ PriceCache (threading.Lock)    │◄───────────────────────┘
                         │  ticker → PriceUpdate          │
                         │  version: int (bumps on write) │
                         └──────┬───────────┬──────────┬──┘
                                │           │          │
             GET /api/stream/prices   portfolio      trade execution,
             (SSE, 0.5s, by version)  valuation      LLM context
```

**Rules**

1. **Producers push and consumers pull.** Data sources write to the cache on their own schedule. Nobody asks a data source for a price; they read the cache.
2. **The cache is the only shared state.** SSE, trades and the LLM context all see the same numbers.
3. **The data source owns the set of tracked tickers.** The DB watchlist (plus open positions, §13.3) decides *what* that set should be, and the routes keep the source in sync.
4. **Only `PriceUpdate` leaves this package.**

---

## 4. File Layout

```
backend/app/market/
  __init__.py         # public re-exports (unchanged list + normalize_ticker)
  models.py           # PriceUpdate                       [changed: session_open, day_change*]
  cache.py            # PriceCache                         [changed: session_open, snapshot(), locking]
  interface.py        # MarketDataSource ABC, normalize_ticker  [changed: + normalize_ticker]
  seed_prices.py      # constants                          [unchanged]
  simulator.py        # GBMSimulator + SimulatorDataSource [changed: rng seed, normalization, session_open]
  massive_client.py   # MassiveDataSource                  [changed: correct SDK parsing, backoff]
  factory.py          # create_market_data_source          [changed: env-driven poll interval]
  stream.py           # create_stream_router               [changed: per-call router, heartbeat, id]
```

Public imports for the rest of the backend:

```python
from app.market import (
    PriceUpdate, PriceCache, MarketDataSource,
    create_market_data_source, create_stream_router, normalize_ticker,
)
```

---

## 5. Data Model — `models.py`

`PriceUpdate` is an immutable value object. The change is a new
`session_open` field: the reference price for the "daily change".

- **Simulator:** the seed price, which is the price when the ticker started being simulated.
- **Massive:** `prev_day.close` (yesterday's close). This is the reference real terminals use.

```python
"""Data models for market data."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Literal

Direction = Literal["up", "down", "flat"]


@dataclass(frozen=True, slots=True)
class PriceUpdate:
    """Immutable snapshot of a single ticker's price at a point in time."""

    ticker: str
    price: float
    previous_price: float  # price at the previous update (tick-to-tick)
    timestamp: float = field(default_factory=time.time)  # Unix seconds
    session_open: float | None = None  # reference for daily change (prev close / seed)

    # --- tick-to-tick (drives the green/red flash) ---

    @property
    def change(self) -> float:
        return round(self.price - self.previous_price, 4)

    @property
    def change_percent(self) -> float:
        if self.previous_price == 0:
            return 0.0
        return round((self.price - self.previous_price) / self.previous_price * 100, 4)

    @property
    def direction(self) -> Direction:
        if self.price > self.previous_price:
            return "up"
        if self.price < self.previous_price:
            return "down"
        return "flat"

    # --- session / daily (drives the watchlist "daily change %") ---

    @property
    def day_change(self) -> float:
        if not self.session_open:
            return 0.0
        return round(self.price - self.session_open, 4)

    @property
    def day_change_percent(self) -> float:
        if not self.session_open:
            return 0.0
        return round((self.price - self.session_open) / self.session_open * 100, 4)

    def to_dict(self) -> dict:
        """Serialize for JSON / SSE transmission. Single source of the wire format."""
        return {
            "ticker": self.ticker,
            "price": self.price,
            "previous_price": self.previous_price,
            "timestamp": self.timestamp,
            "change": self.change,
            "change_percent": self.change_percent,
            "direction": self.direction,
            "session_open": self.session_open,
            "day_change": self.day_change,
            "day_change_percent": self.day_change_percent,
        }
```

Example:

```python
>>> u = PriceUpdate("AAPL", price=191.20, previous_price=191.15,
...                 timestamp=1707580800.5, session_open=190.00)
>>> u.direction, u.change, u.day_change_percent
('up', 0.05, 0.6316)
>>> u.to_dict()["day_change"]
1.2
```

Design notes:
- `frozen=True, slots=True`: safe to share across tasks and threads, and cheap to create (~20/s).
- Every derived value is a property, so `direction` can never disagree with `price`.
- `session_open` defaults to `None`, so all existing constructor calls and tests keep working.

---

## 6. Price Cache — `cache.py`

Changes from v1:
- `update()` accepts an optional `session_open` and keeps the previous one when it isn't given.
- `version` is read under the lock.
- A new `snapshot()` returns `(version, prices)` atomically for the SSE loop.
- `timestamp=0` is no longer treated as "missing".

```python
"""Thread-safe in-memory price cache."""

from __future__ import annotations

import time
from threading import Lock

from .models import PriceUpdate


class PriceCache:
    """Latest PriceUpdate per ticker.

    Writers: exactly one MarketDataSource (asyncio task, or a worker thread via to_thread).
    Readers: SSE stream, portfolio valuation, trade execution, LLM context builder.

    threading.Lock (not asyncio.Lock) because writes can come from a real OS thread.
    Critical sections are a dict get/set — contention is negligible.
    """

    def __init__(self) -> None:
        self._prices: dict[str, PriceUpdate] = {}
        self._lock = Lock()
        self._version = 0  # monotonically increasing; bumped on every write/remove

    def update(
        self,
        ticker: str,
        price: float,
        timestamp: float | None = None,
        session_open: float | None = None,
    ) -> PriceUpdate:
        """Record a new price. Returns the stored PriceUpdate.

        - First update for a ticker: previous_price == price (direction 'flat').
        - session_open: explicit value wins; otherwise inherit from the previous
          update; otherwise fall back to this first price.
        """
        with self._lock:
            ts = time.time() if timestamp is None else timestamp
            prev = self._prices.get(ticker)
            previous_price = prev.price if prev else price
            if session_open is None:
                session_open = prev.session_open if prev else price

            update = PriceUpdate(
                ticker=ticker,
                price=round(price, 2),
                previous_price=round(previous_price, 2),
                timestamp=ts,
                session_open=round(session_open, 2) if session_open else None,
            )
            self._prices[ticker] = update
            self._version += 1
            return update

    def get(self, ticker: str) -> PriceUpdate | None:
        with self._lock:
            return self._prices.get(ticker)

    def get_price(self, ticker: str) -> float | None:
        update = self.get(ticker)
        return update.price if update else None

    def get_all(self) -> dict[str, PriceUpdate]:
        """Shallow copy — PriceUpdate is immutable so this is a safe snapshot."""
        with self._lock:
            return dict(self._prices)

    def snapshot(self) -> tuple[int, dict[str, PriceUpdate]]:
        """Atomically return (version, prices). Used by the SSE loop."""
        with self._lock:
            return self._version, dict(self._prices)

    def remove(self, ticker: str) -> None:
        with self._lock:
            if self._prices.pop(ticker, None) is not None:
                self._version += 1  # so SSE clients see the ticker disappear

    @property
    def version(self) -> int:
        with self._lock:
            return self._version

    def __len__(self) -> int:
        with self._lock:
            return len(self._prices)

    def __contains__(self, ticker: str) -> bool:
        with self._lock:
            return ticker in self._prices
```

> **Behaviour change:** `remove()` now bumps `version`. Without this, when a
> ticker is removed and nothing else changes (Massive between polls), the SSE
> stream keeps showing the removed ticker until the next poll. Update
> `test_cache.py::test_version_increments` if it asserts exact counts across a
> `remove()`.

Usage:

```python
cache = PriceCache()
cache.update("AAPL", 190.00)                       # session_open = 190.00
u = cache.update("AAPL", 191.90)                   # inherits session_open
assert (u.direction, u.day_change_percent) == ("up", 1.0)
cache.update("MSFT", 415.10, timestamp=1707580800.0, session_open=420.00)
version, prices = cache.snapshot()
```

---

## 7. Unified API — `interface.py`

### 7.1 The contract

The ABC itself is unchanged; its docstrings now spell out the obligations both
implementations must meet:

```python
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
    asyncio task. Consumers never call the source for prices — they read the cache.

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
```

### 7.2 Why `normalize_ticker` lives here

Both sources, the watchlist routes and the LLM trade executor need the *same*
canonical form. Putting it next to the interface makes it part of the contract.
Routes call it to return `400` on bad input; the sources call it defensively.

```python
>>> normalize_ticker(" pypl ")
'PYPL'
>>> normalize_ticker("BRK.B")
'BRK.B'
>>> normalize_ticker("not a ticker")
ValueError: Invalid ticker symbol: 'not a ticker'
```

### 7.3 Package exports — `__init__.py`

```python
from .cache import PriceCache
from .factory import create_market_data_source
from .interface import MarketDataSource, normalize_ticker
from .models import PriceUpdate
from .stream import create_stream_router

__all__ = [
    "PriceUpdate",
    "PriceCache",
    "MarketDataSource",
    "normalize_ticker",
    "create_market_data_source",
    "create_stream_router",
]
```

---

## 8. Seed Data — `seed_prices.py`

Unchanged. It holds the default ten tickers' seed prices, per-ticker `sigma`/`mu`,
`DEFAULT_PARAMS` for unknown tickers, and the correlation constants:

| Pair | ρ |
|---|---|
| tech ↔ tech (AAPL, GOOGL, MSFT, AMZN, META, NVDA, NFLX) | 0.6 |
| finance ↔ finance (JPM, V) | 0.5 |
| TSLA ↔ anything | 0.3 |
| cross-sector / unknown | 0.3 |

This matrix is positive definite for any subset of tickers, because every
off-diagonal entry is in [0.3, 0.6] with a block structure, so the Cholesky
decomposition always succeeds. A test asserts this for the full default set and
for 50 random unknown tickers (§15.2).

---

## 9. GBM Simulator — `simulator.py`

### 9.1 `GBMSimulator`: the math engine

$$S_{t+\Delta t} = S_t \cdot \exp\left[\left(\mu - \tfrac{\sigma^2}{2}\right)\Delta t + \sigma\sqrt{\Delta t}\,Z_t\right], \qquad Z_t = L\,\varepsilon_t,\; \varepsilon_t \sim \mathcal N(0, I)$$

where $L$ is the Cholesky factor of the correlation matrix and
$\Delta t = 0.5 / (252 \cdot 6.5 \cdot 3600) \approx 8.48\times10^{-8}$ trading years.

**Magnitude check.** With AAPL at σ = 0.22, one tick has a std of
σ·√Δt ≈ 0.0064 %, about **1.2¢** on $190. That gives a visible flicker every
tick without runaway drift. Over one simulated hour (7,200 ticks) the std is
about 0.55 %. Shock events (p = 0.001 per ticker per tick, ±2–5 %) add a visible
jump about every 50 s across 10 tickers.

Changes from v1:
- An injectable `seed` uses a private `numpy.random.Generator` instead of global state, so tests are deterministic.
- Tickers are normalized.
- New `get_session_open()`.

```python
"""GBM-based market simulator."""

from __future__ import annotations

import asyncio
import logging
import math

import numpy as np

from .cache import PriceCache
from .interface import MarketDataSource, normalize_ticker
from .seed_prices import (
    CORRELATION_GROUPS,
    CROSS_GROUP_CORR,
    DEFAULT_PARAMS,
    INTRA_FINANCE_CORR,
    INTRA_TECH_CORR,
    SEED_PRICES,
    TICKER_PARAMS,
    TSLA_CORR,
)

logger = logging.getLogger(__name__)


class GBMSimulator:
    """Correlated Geometric Brownian Motion for a dynamic set of tickers."""

    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600  # 5,896,800
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR  # ~8.48e-8

    def __init__(
        self,
        tickers: list[str],
        dt: float = DEFAULT_DT,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._dt = dt
        self._sqrt_dt = math.sqrt(dt)
        self._event_prob = event_probability
        self._rng = np.random.default_rng(seed)

        self._tickers: list[str] = []          # order == row order of the Cholesky factor
        self._prices: dict[str, float] = {}
        self._session_open: dict[str, float] = {}
        self._params: dict[str, dict[str, float]] = {}
        self._cholesky: np.ndarray | None = None

        for t in tickers:
            self._add_ticker_internal(normalize_ticker(t))
        self._rebuild_cholesky()

    # --- Public API ---

    def step(self) -> dict[str, float]:
        """Advance every ticker one dt. Returns {ticker: price rounded to cents}."""
        n = len(self._tickers)
        if n == 0:
            return {}

        z = self._rng.standard_normal(n)
        if self._cholesky is not None:
            z = self._cholesky @ z

        shocks = self._rng.random(n) < self._event_prob  # vectorized event draw

        result: dict[str, float] = {}
        for i, ticker in enumerate(self._tickers):
            mu = self._params[ticker]["mu"]
            sigma = self._params[ticker]["sigma"]

            drift = (mu - 0.5 * sigma * sigma) * self._dt
            diffusion = sigma * self._sqrt_dt * z[i]
            price = self._prices[ticker] * math.exp(drift + diffusion)

            if shocks[i]:
                magnitude = self._rng.uniform(0.02, 0.05)
                sign = 1.0 if self._rng.random() < 0.5 else -1.0
                price *= 1.0 + sign * magnitude
                logger.debug("Event on %s: %+.1f%%", ticker, sign * magnitude * 100)

            self._prices[ticker] = price
            result[ticker] = round(price, 2)
        return result

    def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker in self._prices:
            return
        self._add_ticker_internal(ticker)
        self._rebuild_cholesky()

    def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if ticker not in self._prices:
            return
        self._tickers.remove(ticker)
        del self._prices[ticker], self._session_open[ticker], self._params[ticker]
        self._rebuild_cholesky()

    def get_price(self, ticker: str) -> float | None:
        return self._prices.get(ticker)

    def get_session_open(self, ticker: str) -> float | None:
        return self._session_open.get(ticker)

    def get_tickers(self) -> list[str]:
        return list(self._tickers)

    # --- Internals ---

    def _add_ticker_internal(self, ticker: str) -> None:
        if ticker in self._prices:
            return
        seed_price = SEED_PRICES.get(ticker)
        if seed_price is None:
            seed_price = round(float(self._rng.uniform(50.0, 300.0)), 2)
        self._tickers.append(ticker)
        self._prices[ticker] = seed_price
        self._session_open[ticker] = seed_price
        self._params[ticker] = dict(TICKER_PARAMS.get(ticker, DEFAULT_PARAMS))

    def _rebuild_cholesky(self) -> None:
        """O(n^2) build + O(n^3) factorization; n < 50 so this is microseconds."""
        n = len(self._tickers)
        if n <= 1:
            self._cholesky = None
            return
        corr = np.eye(n)
        for i in range(n):
            for j in range(i + 1, n):
                rho = self._pairwise_correlation(self._tickers[i], self._tickers[j])
                corr[i, j] = corr[j, i] = rho
        self._cholesky = np.linalg.cholesky(corr)

    @staticmethod
    def _pairwise_correlation(t1: str, t2: str) -> float:
        tech = CORRELATION_GROUPS["tech"]
        finance = CORRELATION_GROUPS["finance"]
        if "TSLA" in (t1, t2):
            return TSLA_CORR
        if t1 in tech and t2 in tech:
            return INTRA_TECH_CORR
        if t1 in finance and t2 in finance:
            return INTRA_FINANCE_CORR
        return CROSS_GROUP_CORR
```

Example (deterministic):

```python
sim = GBMSimulator(["AAPL", "MSFT", "JPM"], seed=42)
sim.get_price("AAPL")        # 190.0 (seed)
for _ in range(7200):        # one simulated hour
    prices = sim.step()
prices                       # e.g. {'AAPL': 191.37, 'MSFT': 423.02, 'JPM': 194.61}
sim.add_ticker("pypl")       # normalized -> 'PYPL', random seed in [50, 300]
```

### 9.2 `SimulatorDataSource`: the async adapter

```python
class SimulatorDataSource(MarketDataSource):
    """MarketDataSource that steps a GBMSimulator every `update_interval` seconds."""

    def __init__(
        self,
        price_cache: PriceCache,
        update_interval: float = 0.5,
        event_probability: float = 0.001,
        seed: int | None = None,
    ) -> None:
        self._cache = price_cache
        self._interval = update_interval
        self._event_prob = event_probability
        self._seed = seed
        self._sim: GBMSimulator | None = None
        self._task: asyncio.Task | None = None

    async def start(self, tickers: list[str]) -> None:
        self._sim = GBMSimulator(
            tickers=tickers, event_probability=self._event_prob, seed=self._seed
        )
        for ticker in self._sim.get_tickers():   # seed cache → first SSE event is full
            self._seed_cache(ticker)
        self._task = asyncio.create_task(self._run_loop(), name="simulator-loop")
        logger.info("Simulator started with %d tickers", len(self._sim.get_tickers()))

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        self._task = None
        logger.info("Simulator stopped")

    async def add_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim is None or ticker in self._sim.get_tickers():
            return
        self._sim.add_ticker(ticker)
        self._seed_cache(ticker)  # price available immediately → trade-able at once
        logger.info("Simulator: added %s", ticker)

    async def remove_ticker(self, ticker: str) -> None:
        ticker = normalize_ticker(ticker)
        if self._sim:
            self._sim.remove_ticker(ticker)
        self._cache.remove(ticker)
        logger.info("Simulator: removed %s", ticker)

    def get_tickers(self) -> list[str]:
        return self._sim.get_tickers() if self._sim else []

    # --- Internals ---

    def _seed_cache(self, ticker: str) -> None:
        assert self._sim is not None
        price = self._sim.get_price(ticker)
        if price is not None:
            self._cache.update(ticker, price, session_open=self._sim.get_session_open(ticker))

    async def _run_loop(self) -> None:
        while True:
            try:
                if self._sim:
                    for ticker, price in self._sim.step().items():
                        self._cache.update(ticker, price)  # session_open inherited
            except Exception:
                logger.exception("Simulator step failed")  # never let the feed die
            await asyncio.sleep(self._interval)
```

Notes:
- `step()` takes about 20 µs for 10 tickers, so it runs on the event loop. There is no need for `to_thread`.
- Everything runs on one event loop, so `add_ticker`/`remove_ticker` can never interleave with `step()`. That is why `GBMSimulator` needs no lock of its own.
- The sleep is fixed rather than drift-corrected. A tick of jitter is invisible, and the SSE layer re-samples at its own cadence anyway.

---

## 10. Massive API Client — `massive_client.py`

### 10.1 Endpoint

One request per poll covers every tracked ticker:

```
GET https://api.massive.com/v2/snapshot/locale/us/markets/stocks/tickers?tickers=AAPL,GOOGL,MSFT
Authorization: Bearer <MASSIVE_API_KEY>
```

SDK call (synchronous, `urllib3` under the hood, 3 built-in retries on 5xx):

```python
from massive import RESTClient
from massive.rest.models import SnapshotMarketType

client = RESTClient(api_key=key)
snaps = client.get_snapshot_all(market_type=SnapshotMarketType.STOCKS, tickers=["AAPL", "MSFT"])
```

Non-200 responses raise `massive.exceptions.BadResponse(<body text>)`. The body
is JSON such as `{"status":"ERROR","error":"..."}` or `{"status":"NOT_AUTHORIZED",...}`.

### 10.2 Response → SDK model (verified against the installed SDK)

Raw JSON for one ticker (abridged):

```json
{
  "ticker": "AAPL",
  "todaysChange": 1.25, "todaysChangePerc": 0.66, "updated": 1707580800123456789,
  "day":     {"o": 189.6, "h": 191.0, "l": 189.1, "c": 190.85, "v": 51234567, "vw": 190.2},
  "prevDay": {"o": 188.0, "h": 190.1, "l": 187.5, "c": 189.60, "v": 48000000, "vw": 189.0},
  "min":     {"o": 190.8, "h": 190.9, "l": 190.7, "c": 190.85, "v": 12000, "t": 1707580740000},
  "lastTrade": {"p": 190.85, "s": 100, "x": 4, "t": 1707580800123456789},
  "lastQuote": {"p": 190.84, "P": 190.86, "s": 3, "S": 2, "t": 1707580800120000000}
}
```

| We need | SDK attribute | Notes |
|---|---|---|
| current price | `snap.last_trade.price` (`"p"`) | primary |
| fallback price | `snap.min.close` → `snap.day.close` → `snap.prev_day.close` | when there's no last trade (pre-market / thin tickers / plan limits) |
| timestamp | `snap.last_trade.sip_timestamp` (`"t"`) → `snap.updated` | **nanoseconds**. **There is no `last_trade.timestamp`** (bug G1) |
| daily reference | `snap.prev_day.close` (`"prevDay.c"`) | used as `session_open` |
| ticker | `snap.ticker` | |

`Agg` (used by `day`, `prev_day` and `min`) has `open/high/low/close/volume/vwap/timestamp`.
It has no `previous_close` or `change_percent`; those fields belong to the
options snapshot models, not stocks.

### 10.3 Implementation

```python
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


def to_unix_seconds(ts: int | float | None) -> float | None:
    """Massive mixes ns / ms timestamps across endpoints — normalize by magnitude."""
    if ts is None or ts <= 0:
        return None
    if ts > 1e17:   # nanoseconds
        return ts / 1e9
    if ts > 1e14:   # microseconds
        return ts / 1e6
    if ts > 1e11:   # milliseconds
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

    Rate limits: free tier = 5 req/min → default 15 s interval (4 req/min).
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
        logger.info("Massive poller started: %d tickers, %.1fs interval",
                    len(self._tickers), self._interval)

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
        return min(self._interval * 2 ** self._consecutive_failures, MAX_BACKOFF_SECONDS)

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
        except Exception as e:  # BadResponse (401/403/429), urllib3 errors, timeouts
            self._consecutive_failures += 1
            logger.error("Massive poll failed (%d in a row, next try in %.0fs): %s",
                         self._consecutive_failures, self._next_delay(), e)
            return

        self._consecutive_failures = 0
        tracked = set(self._tickers)  # may have changed while we were awaiting
        seen: set[str] = set()
        for snap in snapshots:
            quote = parse_snapshot(snap)
            if quote is None:
                logger.warning("Skipping unusable snapshot for %s",
                               getattr(snap, "ticker", "???"))
                continue
            if quote.ticker not in tracked:
                continue  # removed mid-flight — don't resurrect it in the cache
            self._cache.update(quote.ticker, quote.price,
                               timestamp=quote.timestamp, session_open=quote.session_open)
            seen.add(quote.ticker)

        missing = set(requested) - seen
        if missing:
            logger.warning("Massive returned no data for: %s", ", ".join(sorted(missing)))
        logger.debug("Massive poll: %d/%d tickers updated", len(seen), len(requested))

    def _fetch_snapshots(self, tickers: list[str]) -> list:
        """Blocking HTTP call — always run via asyncio.to_thread."""
        assert self._client is not None
        return self._client.get_snapshot_all(
            market_type=SnapshotMarketType.STOCKS,
            tickers=tickers,
        )
```

Why the design looks like this:
- **`to_thread`**: `RESTClient` is blocking. Running it on the loop would freeze SSE for the whole request.
- **The `requested` copy**: `add_ticker` can mutate `self._tickers` while the worker thread is serializing the ticker list. Passing a copy removes that race.
- **The `tracked` re-check**: if a ticker is removed during the ~200 ms request, the response must not re-insert it into the cache.
- **Backoff instead of a tight retry**: a 429 on the free tier means "slow down". Doubling the interval to 30 s → 60 s → 120 s respects the limit and recovers by itself.
- **Fallback prices**: outside market hours `last_trade` can be missing on some plans. The minute/day/previous close still give a sensible number, so the UI is never blank.

### 10.4 Failure matrix

| Condition | What happens | User sees |
|---|---|---|
| 401 bad key / 403 plan lacks snapshots | `BadResponse` is logged and backoff grows to 120 s | No prices. Logs say `NOT_AUTHORIZED`. Fix `.env` and restart. |
| 429 rate limit | Backoff 30 → 60 → 120 s, reset on the first success | Prices update less often, then recover |
| Network / DNS / timeout | Same as above (SDK retries 5xx 3× first) | Last known prices stay on screen |
| Unknown ticker (`ZZZZ`) | Missing from the response, logged as a warning | Ticker in the watchlist with no price. Trades return `400` (§16). |
| Market closed | Same price each poll. `version` still bumps, so the SSE resend is harmless. | Steady prices with a daily change vs the previous close |

---

## 11. Factory & Configuration — `factory.py`

```python
"""Factory for creating market data sources."""

from __future__ import annotations

import logging
import os

from .cache import PriceCache
from .interface import MarketDataSource
from .massive_client import MassiveDataSource
from .simulator import SimulatorDataSource

logger = logging.getLogger(__name__)

DEFAULT_MASSIVE_POLL_INTERVAL = 15.0
MIN_MASSIVE_POLL_INTERVAL = 1.0


def _float_env(name: str, default: float, minimum: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return max(float(raw), minimum)
    except ValueError:
        logger.warning("Ignoring invalid %s=%r; using %.1f", name, raw, default)
        return default


def create_market_data_source(price_cache: PriceCache) -> MarketDataSource:
    """Return an *unstarted* source. Caller must `await source.start(tickers)`.

    MASSIVE_API_KEY non-empty → MassiveDataSource, else SimulatorDataSource.
    """
    api_key = os.environ.get("MASSIVE_API_KEY", "").strip()
    if api_key:
        interval = _float_env(
            "MASSIVE_POLL_INTERVAL", DEFAULT_MASSIVE_POLL_INTERVAL, MIN_MASSIVE_POLL_INTERVAL
        )
        logger.info("Market data source: Massive API (poll every %.1fs)", interval)
        return MassiveDataSource(api_key=api_key, price_cache=price_cache, poll_interval=interval)

    logger.info("Market data source: GBM simulator")
    return SimulatorDataSource(price_cache=price_cache)
```

### Configuration reference

| Setting | Where | Default | Meaning |
|---|---|---|---|
| `MASSIVE_API_KEY` | env / `.env` | empty | Non-empty switches to real data |
| `MASSIVE_POLL_INTERVAL` | env / `.env` (**new**, optional) | `15` | Seconds between polls (min 1). Free tier: keep ≥ 12. |
| `update_interval` | `SimulatorDataSource(...)` | `0.5` s | Simulator tick |
| `event_probability` | `SimulatorDataSource(...)` | `0.001` | Shock chance per ticker per tick |
| `seed` | `SimulatorDataSource(...)` | `None` | Deterministic simulator (tests) |
| SSE interval | `create_stream_router(..., interval=)` | `0.5` s | Push cadence |
| SSE heartbeat | `create_stream_router(..., heartbeat=)` | `15` s | Comment line sent when nothing changed |
| SSE `retry` | stream | `1000` ms | Browser reconnect delay |

Add `MASSIVE_POLL_INTERVAL=` (commented) to `.env.example`.

---

## 12. SSE Streaming — `stream.py`

Changes from v1:
- A **new router is created on every call** (fixes G3).
- Each event carries an `id:` line set to the cache version.
- A heartbeat comment is sent when idle.
- `PriceCache.snapshot()` gives an atomic read.

```python
"""SSE streaming endpoint for live price updates."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache

logger = logging.getLogger(__name__)

SSE_HEADERS = {
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # disable proxy buffering (nginx, App Runner)
}


def create_stream_router(
    price_cache: PriceCache,
    interval: float = 0.5,
    heartbeat: float = 15.0,
) -> APIRouter:
    """Build a fresh router exposing GET /api/stream/prices."""
    router = APIRouter(prefix="/api/stream", tags=["streaming"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            generate_price_events(price_cache, request, interval, heartbeat),
            media_type="text/event-stream",
            headers=SSE_HEADERS,
        )

    return router


async def generate_price_events(
    price_cache: PriceCache,
    request: Request,
    interval: float = 0.5,
    heartbeat: float = 15.0,
) -> AsyncGenerator[str, None]:
    """Yield SSE frames: full price map whenever the cache version changes."""
    yield "retry: 1000\n\n"

    client = request.client.host if request.client else "unknown"
    logger.info("SSE client connected: %s", client)
    last_version = -1
    last_sent = time.monotonic()

    try:
        while not await request.is_disconnected():
            version, prices = price_cache.snapshot()
            now = time.monotonic()
            if version != last_version:
                last_version = version
                payload = json.dumps({t: u.to_dict() for t, u in prices.items()})
                yield f"id: {version}\ndata: {payload}\n\n"
                last_sent = now
            elif now - last_sent >= heartbeat:
                yield ": ping\n\n"  # comment frame; ignored by EventSource
                last_sent = now
            await asyncio.sleep(interval)
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("SSE client disconnected: %s", client)
```

### Wire format

```
retry: 1000

id: 4182
data: {"AAPL":{"ticker":"AAPL","price":190.52,"previous_price":190.47,"timestamp":1707580800.5,"change":0.05,"change_percent":0.0263,"direction":"up","session_open":190.0,"day_change":0.52,"day_change_percent":0.2737},"MSFT":{...}}

: ping

```

Design decisions:
- **Send the whole map, not deltas.** With 10–30 tickers that is about 3–8 KB every 500 ms, which is trivial on localhost. Every frame is self-contained, so a reconnecting client is fully up to date after one event and needs no `Last-Event-ID` replay logic. The `id:` is informational only.
- **Empty map.** When the watchlist is empty the stream sends `data: {}`. That differs from v1, which sent nothing; now the client can clear removed rows.
- **Fixed cadence.** Sparklines on the frontend sample the stream, and evenly spaced points render cleanly.

---

## 13. FastAPI Integration

These pieces live outside `app/market/`, but they are the contract the backend
agent must implement.

### 13.1 Lifespan (`backend/app/main.py`)

```python
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request

from app.market import MarketDataSource, PriceCache, create_market_data_source, create_stream_router
from app.db import init_db, get_tracked_tickers  # backend agent: watchlist ∪ open positions


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()                                          # lazy schema + seed (PLAN §7)
    cache = PriceCache()
    source = create_market_data_source(cache)
    await source.start(get_tracked_tickers(user_id="default"))

    app.state.price_cache = cache
    app.state.market_source = source
    app.include_router(create_stream_router(cache))
    try:
        yield
    finally:
        await source.stop()


app = FastAPI(title="FinAlly", lifespan=lifespan)


def get_price_cache(request: Request) -> PriceCache:
    return request.app.state.price_cache


def get_market_source(request: Request) -> MarketDataSource:
    return request.app.state.market_source
```

> The dependency getters take `request: Request` instead of closing over the
> module-level `app`, so tests can build a separate app instance.
>
> Including the router inside the lifespan works. If the backend agent prefers
> static registration, an alternative is to create the cache at module import
> and call `app.include_router(create_stream_router(cache))` at module level.
> Only `source.start()` / `stop()` must stay inside the lifespan.

### 13.2 Reading prices (trades, portfolio, LLM context)

```python
from fastapi import Depends, HTTPException

@router.post("/api/portfolio/trade")
async def execute_trade(req: TradeRequest, cache: PriceCache = Depends(get_price_cache)):
    ticker = normalize_ticker(req.ticker)
    price = cache.get_price(ticker)
    if price is None:
        raise HTTPException(400, f"No live price for {ticker} yet — add it to the watchlist "
                                 "or wait a moment and retry.")
    # ... validate cash/shares, fill at `price`, record snapshot ...
```

Portfolio valuation should take a **single** `cache.get_all()` snapshot per
request, so all positions are valued at one consistent instant.

### 13.3 Keeping tracked tickers in sync

**Tracked set = watchlist ∪ tickers with an open position.** This keeps
held-but-unwatched positions priced (heatmap, P&L, total value).

```python
async def sync_ticker(ticker: str, source: MarketDataSource, db) -> None:
    """Call after any watchlist change or trade that may open/close a position."""
    should_track = db.is_in_watchlist(ticker) or db.position_qty(ticker) > 0
    is_tracked = ticker in source.get_tickers()
    if should_track and not is_tracked:
        await source.add_ticker(ticker)
    elif not should_track and is_tracked:
        await source.remove_ticker(ticker)
```

| Event | Calls |
|---|---|
| `POST /api/watchlist {ticker}` | `normalize_ticker` (400 on `ValueError`) → DB insert → `sync_ticker` |
| `DELETE /api/watchlist/{ticker}` | DB delete → `sync_ticker` (keeps tracking if still held) |
| Trade fully closes a position | `sync_ticker` (stops tracking if not watched) |
| LLM `watchlist_changes` | same code path as the REST routes |

`POST /api/watchlist` returns the current price when the cache already has it.
The simulator always has it, because `add_ticker` seeds the cache. Massive
returns `null` until the next poll; the frontend shows "—" until SSE delivers it.

---

## 14. Frontend Contract

```ts
// types
export interface PriceUpdate {
  ticker: string;
  price: number;
  previous_price: number;
  timestamp: number;          // unix seconds
  change: number;
  change_percent: number;
  direction: "up" | "down" | "flat";
  session_open: number | null;
  day_change: number;
  day_change_percent: number; // ← watchlist "daily change %"
}
export type PriceMap = Record<string, PriceUpdate>;

// hook sketch
const es = new EventSource("/api/stream/prices");
es.onopen = () => setStatus("connected");                 // green dot
es.onerror = () =>
  setStatus(es.readyState === EventSource.CLOSED ? "disconnected" : "reconnecting");
es.onmessage = (e) => {
  const prices: PriceMap = JSON.parse(e.data);
  // 1) replace the price map (tickers absent from the map were removed)
  // 2) for each ticker whose `price` differs from the last seen value, push
  //    {time: timestamp, value: price} to its sparkline buffer (cap ~600 pts)
  // 3) flash class from `direction` when price changed
};
```

The frontend should flash only when `price` actually changed since the last
frame it rendered. The server resends the full map, so an unchanged ticker
arrives again with its old `direction`.

---

## 15. Testing

Run with `cd backend && uv run --extra dev pytest -v`.

### 15.1 Models & cache

```python
def test_day_change_uses_session_open():
    c = PriceCache()
    c.update("AAPL", 190.00)
    u = c.update("AAPL", 191.90)
    assert u.session_open == 190.00
    assert u.day_change_percent == 1.0
    assert u.to_dict()["day_change"] == 1.9

def test_explicit_session_open_wins():
    u = PriceCache().update("MSFT", 415.0, session_open=420.0)
    assert u.day_change == -5.0

def test_zero_timestamp_is_respected():
    assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0

def test_remove_bumps_version():
    c = PriceCache(); c.update("AAPL", 1.0); v = c.version
    c.remove("AAPL"); assert c.version == v + 1
    c.remove("AAPL"); assert c.version == v + 1   # no-op doesn't bump

def test_snapshot_is_atomic_pair():
    c = PriceCache(); c.update("AAPL", 1.0)
    version, prices = c.snapshot()
    assert version == c.version and set(prices) == {"AAPL"}

def test_concurrent_writers():
    import threading
    c = PriceCache()
    def w(t): [c.update(t, 100 + i) for i in range(1000)]
    ts = [threading.Thread(target=w, args=(f"T{i}",)) for i in range(8)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert c.version == 8000 and len(c) == 8
```

### 15.2 Simulator

```python
def test_seeded_simulator_is_deterministic():
    a = GBMSimulator(["AAPL", "TSLA"], seed=7)
    b = GBMSimulator(["AAPL", "TSLA"], seed=7)
    assert [a.step() for _ in range(100)] == [b.step() for _ in range(100)]

def test_cholesky_full_default_set_and_many_unknowns():
    GBMSimulator(list(SEED_PRICES))                              # must not raise
    GBMSimulator(list(SEED_PRICES) + [f"ZZ{i}" for i in range(40)])

def test_ticker_normalization():
    sim = GBMSimulator(["aapl"])
    sim.add_ticker(" AAPL ")
    assert sim.get_tickers() == ["AAPL"]

def test_tick_volatility_is_realistic():
    sim = GBMSimulator(["AAPL"], event_probability=0.0, seed=1)
    prices = [sim.step()["AAPL"] for _ in range(5000)]
    rets = np.diff(np.log(prices))
    assert 0.00003 < rets.std() < 0.0002          # ≈ 0.22 * sqrt(dt) ≈ 6.4e-5

def test_events_fire():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=3)
    p0 = sim.get_price("AAPL"); p1 = sim.step()["AAPL"]
    assert 0.019 < abs(p1 / p0 - 1) < 0.051
```

### 15.3 SimulatorDataSource

```python
async def test_add_ticker_seeds_cache_with_session_open():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    await src.add_ticker("pypl")
    u = cache.get("PYPL")
    assert u is not None and u.session_open == u.price
    await asyncio.sleep(0.05)
    assert cache.get("AAPL").session_open == 190.0     # inherited across ticks
    await src.stop(); await src.stop()                 # idempotent
```

### 15.4 Massive: use **real SDK models**, not `MagicMock`

This is the test that would have caught bug G1. Build fixtures with the SDK's
own deserializer so that attribute names are checked.

```python
from massive.rest.models import TickerSnapshot
from app.market.massive_client import MassiveDataSource, parse_snapshot, to_unix_seconds

def snap(ticker="AAPL", price=190.85, prev_close=189.60, t_ns=1_707_580_800_123_456_789, **extra):
    d = {"ticker": ticker, "updated": t_ns,
         "prevDay": {"c": prev_close}, "day": {"c": price},
         "lastTrade": {"p": price, "s": 100, "t": t_ns}}
    d.update(extra)
    return TickerSnapshot.from_dict(d)

def test_parse_real_snapshot():
    q = parse_snapshot(snap())
    assert (q.ticker, q.price, q.session_open) == ("AAPL", 190.85, 189.60)
    assert q.timestamp == pytest.approx(1_707_580_800.123, abs=1e-3)

def test_parse_falls_back_when_no_last_trade():
    s = TickerSnapshot.from_dict({"ticker": "AAPL", "day": {"c": 0}, "prevDay": {"c": 189.6}})
    assert parse_snapshot(s).price == 189.6

def test_parse_unusable_returns_none():
    assert parse_snapshot(TickerSnapshot.from_dict({"ticker": "AAPL"})) is None

@pytest.mark.parametrize("raw,expected", [
    (1_707_580_800, 1_707_580_800.0),
    (1_707_580_800_000, 1_707_580_800.0),
    (1_707_580_800_000_000_000, 1_707_580_800.0),
    (None, None), (0, None),
])
def test_to_unix_seconds(raw, expected):
    assert to_unix_seconds(raw) == expected

async def test_poll_once_updates_cache_and_ignores_removed():
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60)
    src._client = object()
    src._tickers = ["AAPL"]                           # GOOGL was removed mid-flight
    src._fetch_snapshots = lambda tickers: [snap("AAPL"), snap("GOOGL", 175.0, 174.0)]
    await src._poll_once()
    assert cache.get_price("AAPL") == 190.85
    assert cache.get("AAPL").day_change == 1.25
    assert "GOOGL" not in cache

async def test_backoff_grows_and_resets():
    src = MassiveDataSource("k", PriceCache(), poll_interval=15)
    src._client = object(); src._tickers = ["AAPL"]
    def boom(_): raise RuntimeError("429")
    src._fetch_snapshots = boom
    await src._poll_once(); await src._poll_once()
    assert src._next_delay() == 60
    src._fetch_snapshots = lambda _: [snap()]
    await src._poll_once()
    assert src._next_delay() == 15
```

### 15.5 SSE

Test the generator directly with a fake request. That is faster and more
reliable than streaming through an HTTP client.

```python
class FakeRequest:
    def __init__(self, polls: int): self._left = polls; self.client = None
    async def is_disconnected(self):
        self._left -= 1
        return self._left < 0

async def test_sse_emits_retry_then_data_then_stops():
    cache = PriceCache(); cache.update("AAPL", 190.0)
    frames = [f async for f in generate_price_events(cache, FakeRequest(2), interval=0)]
    assert frames[0] == "retry: 1000\n\n"
    assert frames[1].startswith("id: 1\ndata: ")
    assert json.loads(frames[1].split("data: ", 1)[1])["AAPL"]["price"] == 190.0
    assert len(frames) == 2                      # version unchanged → no duplicate

async def test_sse_heartbeat_when_idle():
    cache = PriceCache(); cache.update("AAPL", 190.0)
    frames = [f async for f in generate_price_events(cache, FakeRequest(3), interval=0, heartbeat=0)]
    assert ": ping\n\n" in frames

def test_create_stream_router_is_idempotent():
    c = PriceCache()
    r1, r2 = create_stream_router(c), create_stream_router(c)
    assert r1 is not r2 and len(r1.routes) == len(r2.routes) == 1
```

### 15.6 Optional live smoke test

```python
@pytest.mark.skipif(not os.getenv("MASSIVE_API_KEY"), reason="needs real key")
async def test_massive_live():
    cache = PriceCache()
    src = MassiveDataSource(os.environ["MASSIVE_API_KEY"], cache)
    await src.start(["AAPL"]); await src.stop()
    assert cache.get_price("AAPL") is not None
```

---

## 16. Edge Cases & Failure Modes

| Case | Behaviour |
|---|---|
| Empty watchlist at startup | Sources start with `[]`, the simulator returns `{}` per step, Massive skips the call, SSE sends `data: {}` |
| Trade on a ticker with no cached price | `400` with an explanatory message (§13.2). Never fill at a stale or guessed price. |
| Invalid ticker (`"AAPL;DROP"`, `""`) | `normalize_ticker` raises `ValueError` and the route returns `400` |
| Unknown but well-formed ticker | Simulator: random seed $50–$300, default σ/μ, ρ = 0.3. Massive: no data, warning logged, no price. |
| Removed ticker still held | Kept in the tracked set via `sync_ticker` (§13.3) |
| Ticker removed during an in-flight Massive poll | Ignored on arrival (`tracked` re-check) |
| Simulator step throws | Logged with traceback; the loop continues next tick |
| Massive down / 401 / 429 | Exponential backoff to 120 s; last prices stay in the cache |
| SSE client disconnects | `is_disconnected()` ends the generator; the task is cancelled on shutdown |
| Many SSE clients | Each does one `snapshot()` + `json.dumps` per 0.5 s. Fine for dozens. If it ever matters, serialize once per version and share the string. |
| Prices after a restart | Simulator restarts from seed prices (no persistence by design). Massive refills on the first poll. |

---

## 17. Implementation Checklist

Work in this order. Each step should leave the full test suite green.

1. **`models.py`**: add `session_open`, `day_change` and `day_change_percent`, and extend `to_dict()`.
2. **`cache.py`**: add the `session_open` parameter and `snapshot()`, read `version` under the lock, make `remove()` bump the version, and use `timestamp is None`. Update `test_cache.py`.
3. **`interface.py`**: add `normalize_ticker` and export it from `__init__.py`.
4. **`simulator.py`**: add the `seed`/`Generator`, normalization, `get_session_open`, and `_seed_cache`. Add the tests from §15.2–15.3.
5. **`massive_client.py`**: add `parse_snapshot`, `to_unix_seconds`, the fallback chain, backoff, the copy-then-recheck ticker handling, and `_fetch_snapshots(tickers)`. **Rewrite `test_massive.py` to use `TickerSnapshot.from_dict` fixtures** (§15.4).
6. **`factory.py`**: add `MASSIVE_POLL_INTERVAL` parsing. Add a test for an invalid value and for the minimum clamp.
7. **`stream.py`**: build the router per call, rename the generator to the public `generate_price_events`, and add `id:`, the heartbeat and `snapshot()`. Add the tests from §15.5.
8. **Docs**: fix `planning/archive/MASSIVE_API.md` field names (G10), add `MASSIVE_POLL_INTERVAL` to `.env.example`, and update `backend/CLAUDE.md` with `normalize_ticker`, `day_change_percent` and `snapshot()`.
9. **`uv run --extra dev ruff check app/ tests/`** and **`pytest --cov=app`**. Target ≥ 90 % for `app/market/`, with `massive_client.py` no longer at 56 %.
10. Run `uv run market_data_demo.py` and confirm it still renders. It only uses `PriceCache`, `SEED_PRICES` and `SimulatorDataSource`, all of which are compatible.
