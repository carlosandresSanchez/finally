# Market Data Interface: Unified Python API

This is the contract for how FinAlly gets stock prices. Downstream code (SSE, portfolio, trades, chat) calls one API and never needs to know whether prices come from the **Massive API** (when `MASSIVE_API_KEY` is set) or the **built-in simulator** (otherwise).

Status: implemented in `backend/app/market/`. §1–§5 describe the code as it is. §6 lists the changes needed in the Massive source, based on the research in `MASSIVE_API.md`. The simulator internals are in `MARKET_SIMULATOR.md`.

---

## 1. Design in one picture

```
                 create_market_data_source(cache)        ← reads MASSIVE_API_KEY
                              │
            ┌─────────────────┴──────────────────┐
            ▼                                    ▼
  SimulatorDataSource                     MassiveDataSource
  (GBM, ticks every 0.5s)                 (REST poll, every 15s by default)
            │   writes                           │   writes
            └──────────────►  PriceCache  ◄──────┘
                                  │  reads (never calls the source for prices)
       ┌──────────────┬───────────┼──────────────┬──────────────┐
       ▼              ▼           ▼              ▼              ▼
  SSE /api/stream  portfolio   trade fill    watchlist API   LLM context
     /prices       valuation   price         (GET prices)    builder
```

Principles:

- **Strategy pattern.** Both sources implement the `MarketDataSource` ABC. Exactly one is active per process.
- **Push, not pull.** Sources push into `PriceCache` on their own schedule. Consumers only ever **read the cache**. A request never makes a network call, so a request handler can't stall on Massive.
- **One writer, many readers.** `PriceCache` is thread-safe (a `threading.Lock`), because the Massive client runs in a worker thread.
- **The source only manages which tickers are tracked.** The watchlist lives in SQLite, owned by the API layer, and the API layer tells the source to add or remove tickers.

---

## 2. Public API (`from app.market import ...`)

### 2.1 `PriceUpdate`: immutable value object (`models.py`)

```python
@dataclass(frozen=True, slots=True)
class PriceUpdate:
    ticker: str
    price: float            # rounded to 2 dp by the cache
    previous_price: float   # price from the previous update (tick-to-tick)
    timestamp: float        # Unix seconds

    change: float           # property: price - previous_price
    change_percent: float   # property: % change vs previous_price
    direction: str          # property: "up" | "down" | "flat"
    def to_dict(self) -> dict  # JSON / SSE shape
```

`to_dict()` is the wire format for the SSE stream:

```json
{"ticker": "AAPL", "price": 190.12, "previous_price": 190.05, "timestamp": 1759740000.5,
 "change": 0.07, "change_percent": 0.0368, "direction": "up"}
```

### 2.2 `PriceCache`: single source of truth (`cache.py`)

| Method | Returns | Use |
|---|---|---|
| `update(ticker, price, timestamp=None)` | `PriceUpdate` | **Sources only.** Works out `previous_price` and direction from the last entry |
| `get(ticker)` | `PriceUpdate \| None` | Full update for one ticker |
| `get_price(ticker)` | `float \| None` | Trade fills and valuation |
| `get_all()` | `dict[str, PriceUpdate]` | SSE payload, watchlist endpoint, LLM context |
| `remove(ticker)` | `None` | Called by sources on `remove_ticker` |
| `version` | `int` | Goes up on every write. SSE only sends when it changes |
| `len(cache)`, `ticker in cache` | | |

### 2.3 `MarketDataSource`: the abstract interface (`interface.py`)

```python
class MarketDataSource(ABC):
    async def start(self, tickers: list[str]) -> None: ...   # seed cache, launch background task; call once
    async def stop(self) -> None: ...                         # cancel task; idempotent
    async def add_ticker(self, ticker: str) -> None: ...      # no-op if present
    async def remove_ticker(self, ticker: str) -> None: ...   # no-op if absent; also evicts from cache
    def get_tickers(self) -> list[str]: ...
```

What every implementation must do:

1. `start()` returns only after the cache holds a first value for every ticker the source can price. The simulator seeds synchronously. Massive runs one poll before returning.
2. The background task **never dies on an error**. It logs, keeps the last good prices in the cache and tries again on the next cycle.
3. `remove_ticker()` evicts the ticker from the cache, so the SSE stream stops sending it.
4. Only the background task and `add_ticker()` write to the cache, and no task survives `stop()`.

### 2.4 `create_market_data_source(cache) -> MarketDataSource` (`factory.py`)

| `MASSIVE_API_KEY` | Source | Notes |
|---|---|---|
| unset, empty or whitespace | `SimulatorDataSource(cache)` | default, no network |
| non-empty | `MassiveDataSource(api_key, cache)` | default poll interval 15s (safe for the free 5 calls/min) |

It returns an **unstarted** source. The caller awaits `start()`.

### 2.5 `create_stream_router(cache) -> APIRouter` (`stream.py`)

`GET /api/stream/prices` (`text/event-stream`):

- It first sends `retry: 1000`, so the browser `EventSource` reconnects after 1 second.
- Every 500ms, if `cache.version` changed, it sends one event containing **all** tickers: `data: {"AAPL": {...PriceUpdate.to_dict()}, "MSFT": {...}}`.
- It stops when `request.is_disconnected()` returns true.

---

## 3. Wiring into FastAPI

```python
# backend/app/main.py (to be written by the backend agent)
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.market import PriceCache, create_market_data_source, create_stream_router

price_cache = PriceCache()
market = create_market_data_source(price_cache)

@asynccontextmanager
async def lifespan(app: FastAPI):
    tickers = db.active_tickers()          # watchlist ∪ open positions, see §4
    await market.start(tickers)
    app.state.prices = price_cache
    app.state.market = market
    yield
    await market.stop()

app = FastAPI(lifespan=lifespan)
app.include_router(create_stream_router(price_cache))
```

How consumers use it:

```python
# Trade execution: fill at the cached price, reject if there isn't one yet
price = app.state.prices.get_price(ticker)
if price is None:
    raise HTTPException(400, f"No price available for {ticker}")

# Portfolio valuation
prices = app.state.prices.get_all()
value = cash + sum(p.quantity * prices[p.ticker].price for p in positions if p.ticker in prices)

# Watchlist add / remove
await app.state.market.add_ticker(ticker)
await app.state.market.remove_ticker(ticker)   # only if no open position, see §4
```

---

## 4. Rules for consumers

1. **Normalize tickers at the API boundary**: `ticker.strip().upper()`. Massive tickers are case-sensitive. The simulator accepts any string, so `aapl` and `AAPL` would otherwise turn into two separate simulated stocks.
2. **Tracked tickers = watchlist ∪ open positions.** If the user removes a ticker from the watchlist while still holding it, the portfolio still needs its price. Only call `remove_ticker` when the ticker is in neither set. Selling a position down to zero should check the watchlist the same way.
3. **`None` means "not priced yet".** Trades must be rejected, not filled at 0. Valuation can fall back to `avg_cost` and mark the value as stale.
4. **Daily change % isn't in `PriceUpdate`.** `change_percent` is tick-to-tick. The watchlist's "daily change %" (`PLAN.md` §10) needs a session reference price. Option A (recommended, simplest): the frontend stores the first price it sees per ticker and computes change against that. Option B: add an optional `session_open` to the cache. Massive provides it as `prev_day.close` and `todays_change_percent`; the simulator would use its seed price.
5. **Don't call the source for prices.** Only use `get_tickers()` and add/remove on it.

---

## 5. Configuration

| Variable | Default | Effect |
|---|---|---|
| `MASSIVE_API_KEY` | empty | non-empty → Massive. Otherwise the simulator |
| `MASSIVE_POLL_INTERVAL` *(proposed)* | `15` | seconds between Massive polls. Paid plans can use 2–5. The factory should pass it to `MassiveDataSource(poll_interval=...)` |

The simulator has its own settings as constructor arguments (`update_interval=0.5`, `event_probability=0.001`). They aren't exposed as environment variables.

---

## 6. Changes needed in `MassiveDataSource`

Researching the API (`MASSIVE_API.md`) turned up gaps between `massive_client.py` and how the API really behaves:

| # | Problem | Effect | Fix |
|---|---|---|---|
| 1 | Reads `snap.last_trade.timestamp`. The client's `LastTrade` model has `sip_timestamp` (in **ns**) and no `timestamp` | `AttributeError` on every snapshot → every ticker is skipped → **the cache stays empty on every plan** | Use the extraction chain below. Convert ns → s |
| 2 | `last_trade` is `None` on the Starter plan (no trades access) | All tickers are skipped on Starter | Fall back to `min.close` → `day.close` → `prev_day.close` |
| 3 | The snapshot endpoint returns 403 `NOT_AUTHORIZED` on free Basic | Every poll fails, so no prices | Detect it once and switch to **EOD mode** (grouped daily, one call) |
| 4 | `start()` doesn't uppercase the initial tickers (`add_ticker` does) | Lowercase tickers silently return nothing | Normalize in `start()` too, and at the API boundary (§4.1) |
| 5 | `test_massive.py` mocks `last_trade.timestamp` | Tests pass and hide #1 | Build mocks from real `TickerSnapshot.from_dict(...)` JSON samples |

### 6.1 Price extraction (replaces the body of the loop in `_poll_once`)

```python
def _extract(snap) -> tuple[float, float] | None:
    """(price, unix_seconds) from the freshest field this plan provides."""
    lt = snap.last_trade
    if lt and lt.price:
        return lt.price, (lt.sip_timestamp or 0) / 1e9 or time.time()
    if snap.min and snap.min.close:
        return snap.min.close, (snap.min.timestamp or 0) / 1e3 or time.time()
    for bar in (snap.day, snap.prev_day):
        if bar and bar.close:                 # 0 after the 3:30 AM ET reset → skip
            return bar.close, (snap.updated or 0) / 1e9 or time.time()
    return None
```

### 6.2 Modes

```
start(tickers)
  └─ poll snapshot ──ok──► SNAPSHOT mode: poll get_snapshot_all every poll_interval
            │
            └─ BadResponse containing NOT_AUTHORIZED / 403
                    └─► EOD mode: get_grouped_daily_aggs(latest trading day), filter to tickers,
                        poll every max(poll_interval, 300s); log one WARNING suggesting the simulator
```

- EOD mode spends 1 call per refresh (plus 1–3 more to step back over weekends and holidays), which stays inside 5 calls/min.
- In EOD mode the prices don't move, so the UI won't flash. That's expected. The startup log should say so clearly.
- On a 429, skip the next poll cycle rather than retrying in a tight loop.

### 6.3 Ticker validation (optional)

A ticker that doesn't exist simply isn't in the snapshot response. To reject one when it's added, `add_ticker` (or the watchlist route) can call `get_snapshot_ticker` once and return 404 when it fails. The simulator accepts any symbol and gives it a random seed price.

---

## 7. Testing the interface

- **Contract tests**, parametrized over both sources. After `start(["AAPL","MSFT"])`, the cache contains both tickers. `add_ticker` makes the new ticker appear. `remove_ticker` evicts it. `stop()` is idempotent and nothing writes afterwards.
- **Massive parsing.** Feed fixtures built with `TickerSnapshot.from_dict(<real JSON>)` for three plan shapes: with `lastTrade` (Developer), without it (Starter), and with `day.c == 0` (pre-market).
- **Mode switch.** Make `get_snapshot_all` raise `BadResponse('{"status":"NOT_AUTHORIZED"}')`, then check that `get_grouped_daily_aggs` is used and the cache fills.
- **Factory.** An empty or whitespace key gives the simulator. A real key gives Massive (`tests/market/test_factory.py` already covers this).
