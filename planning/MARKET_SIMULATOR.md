# Market Simulator: Approach and Code Structure

The simulator is FinAlly's default price source. It runs when `MASSIVE_API_KEY` isn't set. It produces realistic-looking, correlated, live-updating stock prices with no network and no API key, and it plugs into the same `MarketDataSource` / `PriceCache` contract as the Massive client (see `MARKET_INTERFACE.md`).

Status: implemented in `backend/app/market/simulator.py` and `seed_prices.py`, tested in `backend/tests/market/test_simulator*.py`.

---

## 1. Goals

| Goal | How it's met |
|---|---|
| Prices look like real stocks | Geometric Brownian Motion (GBM): log-normal, always positive, volatility scaled to each ticker |
| Stocks move together the way sectors do | Correlated random draws via a Cholesky factor of a sector correlation matrix |
| Something visible keeps happening on screen | A tick every 500ms, plus occasional 2–5% "event" jumps |
| Realistic starting point | Seed prices for the 10 default tickers |
| Any ticker can be added | Unknown tickers get a random seed price and default parameters |
| Testable | The math (`GBMSimulator`) is pure and synchronous. The asyncio wrapper is a thin layer around it |

Out of scope: order-book or bid/ask simulation, market hours, mean reversion, fundamentals and news.

---

## 2. The model

### 2.1 GBM step

For each ticker, on each tick:

```
S(t+dt) = S(t) · exp( (μ − σ²/2)·dt + σ·√dt·Z )
```

| Symbol | Meaning | Source |
|---|---|---|
| `S` | price | starts at `SEED_PRICES[ticker]` |
| `μ` | annual drift | `TICKER_PARAMS[ticker]["mu"]` (0.03–0.08) |
| `σ` | annual volatility | `TICKER_PARAMS[ticker]["sigma"]` (0.17–0.50) |
| `dt` | tick length as a fraction of a trading year | `0.5 / (252 · 6.5 · 3600) ≈ 8.48e-8` |
| `Z` | standard normal, **correlated across tickers** | §2.2 |

The `−σ²/2` term (the Itô correction) means the expected price grows at rate `μ`. Taking the exponential keeps prices positive.

**Size of the moves** (wall-clock time, since each 500ms tick counts as 500ms of trading time):

| Ticker (σ) | 1σ per tick | 1σ per hour of running | 1σ per 8-hour session |
|---|---|---|---|
| V (0.17) | 0.005% (~1.4¢ on $280) | 0.42% | 1.2% |
| AAPL (0.22) | 0.006% (~1.2¢ on $190) | 0.54% | 1.5% |
| NVDA (0.40) | 0.012% (~9¢ on $800) | 0.99% | 2.8% |
| TSLA (0.50) | 0.015% (~4¢ on $250) | 1.24% | 3.5% |

Drift is negligible over a demo session (`μ·dt ≈ 4e-9` per tick).

### 2.2 Correlated draws

1. Build an `n×n` correlation matrix `C` over the tracked tickers, using pairwise rules from `seed_prices.py`:

   | Pair | ρ | Constant |
   |---|---|---|
   | TSLA with anything | 0.3 | `TSLA_CORR` (TSLA is in the tech set but moves on its own) |
   | tech–tech (AAPL GOOGL MSFT AMZN META NVDA NFLX) | 0.6 | `INTRA_TECH_CORR` |
   | finance–finance (JPM V) | 0.5 | `INTRA_FINANCE_CORR` |
   | everything else, including unknown tickers | 0.3 | `CROSS_GROUP_CORR` |

2. Factor it once: `L = cholesky(C)` (`numpy.linalg.cholesky`).
3. On each tick: `z = L @ standard_normal(n)` gives draws with the correlation `C`.

`C` is rebuilt only when a ticker is added or removed. That's O(n³), which is trivial for fewer than 50 tickers. With every off-diagonal value non-negative and the within-group values ≥ the cross-group values, `C` stays positive-definite, so Cholesky can't fail. Any new group must keep that property.

### 2.3 Random events

After each GBM step, each ticker independently has probability `event_probability` (default `0.001`) of jumping by `±U(2%, 5%)`.

- With 10 tickers at 2 ticks/s, there's about **one event every 50 seconds** across the watchlist, or about one every ~8 minutes per ticker.
- **Tuning note:** at the default rate, events dominate the volatility. Each ticker gets ~7 jumps per hour, which works out to ~10% standard deviation per hour versus ~0.5–1.2% from GBM. Prices can wander far from their seeds during a long session. That's good for drama, but `event_probability=0.0002` gives about one event per ticker per ~40 minutes with GBM still driving most of the movement.

### 2.4 Rounding

`step()` returns prices rounded to 2 decimals, and `PriceCache.update` rounds them again. The internal state (`_prices`) keeps full precision, so rounding never builds up.

---

## 3. Code structure

```
backend/app/market/
├── seed_prices.py      # data only: SEED_PRICES, TICKER_PARAMS, DEFAULT_PARAMS,
│                       #   CORRELATION_GROUPS, *_CORR constants
├── simulator.py
│   ├── GBMSimulator          # pure math, synchronous, no I/O
│   └── SimulatorDataSource   # MarketDataSource impl: asyncio loop → PriceCache
├── interface.py        # MarketDataSource ABC
└── cache.py            # PriceCache
```

### 3.1 `GBMSimulator`: the engine

```python
class GBMSimulator:
    TRADING_SECONDS_PER_YEAR = 252 * 6.5 * 3600
    DEFAULT_DT = 0.5 / TRADING_SECONDS_PER_YEAR

    def __init__(self, tickers: list[str], dt: float = DEFAULT_DT, event_probability: float = 0.001): ...
    def step(self) -> dict[str, float]          # advance all tickers one tick; hot path
    def add_ticker(self, ticker: str) -> None   # seed + params, rebuild Cholesky
    def remove_ticker(self, ticker: str) -> None
    def get_price(self, ticker: str) -> float | None
    def get_tickers(self) -> list[str]

    # internals
    _add_ticker_internal(ticker)        # add without rebuilding (batch init)
    _rebuild_cholesky()                 # None when n ≤ 1
    _pairwise_correlation(t1, t2)       # staticmethod, the rules in §2.2
```

State: `_tickers` (ordered list whose order matches the Cholesky rows), `_prices`, `_params` and `_cholesky`.

Unknown tickers: seed price `random.uniform(50, 300)` and `DEFAULT_PARAMS = {sigma: 0.25, mu: 0.05}`.

### 3.2 `SimulatorDataSource`: the async adapter

```python
class SimulatorDataSource(MarketDataSource):
    def __init__(self, price_cache, update_interval=0.5, event_probability=0.001): ...

    async def start(tickers):   # build GBMSimulator, seed cache with initial prices,
                                # create_task(_run_loop)
    async def stop():           # cancel + await task; idempotent
    async def add_ticker(t):    # sim.add_ticker + write seed price to cache immediately
    async def remove_ticker(t): # sim.remove_ticker + cache.remove
    def get_tickers()

    async def _run_loop():      # while True: step() → cache.update(...) ; sleep(interval)
                                # exceptions are logged, the loop keeps going
```

Data flow on each tick:

```
_run_loop ── sim.step() ──► {ticker: price} ── cache.update(t, p) ──► version++ ──► SSE picks it up within 500ms
```

Everything runs on the event loop, with no threads. One `step()` for 10 tickers is a 10×10 matrix-vector product plus 10 `exp` calls, which takes microseconds.

---

## 4. Tuning knobs

| Knob | Where | Default | Effect |
|---|---|---|---|
| Seed prices | `SEED_PRICES` | AAPL 190, GOOGL 175, MSFT 420, AMZN 185, TSLA 250, NVDA 800, META 500, JPM 195, V 280, NFLX 600 | starting levels |
| Per-ticker σ, μ | `TICKER_PARAMS` | see file | how jumpy each ticker is |
| Correlations | `*_CORR`, `CORRELATION_GROUPS` | 0.6 / 0.5 / 0.3 | how much tickers move together |
| Tick rate | `SimulatorDataSource(update_interval=)` | 0.5s | UI cadence. `dt` is fixed at 0.5s of trading time |
| Event rate | `event_probability=` | 0.001 | drama vs. realism (§2.3) |
| Event size | `simulator.py` `uniform(0.02, 0.05)` | 2–5% | jump size |

To add a sector, add a set to `CORRELATION_GROUPS`, a constant for its correlation, and a branch in `_pairwise_correlation`.

---

## 5. Known limitations and recommended follow-ups

1. **Prices reset when the app restarts.** `start()` always seeds from `SEED_PRICES`, while positions in SQLite keep their `avg_cost`. After a restart that follows a long session, unrealized P&L jumps. Follow-up: accept optional `initial_prices` in `start()` (for example, the last trade price per ticker from the `trades` table) and seed from those.
2. **Global random state.** The simulator uses `np.random.standard_normal` and the `random` module. Tests make it deterministic by seeding both globally. Follow-up: inject a `numpy.random.Generator` (`rng=` argument) for isolated, reproducible runs.
3. **Unknown tickers get a new random seed each time they're added**, so removing and re-adding `PYPL` changes its price. That's acceptable for a demo. A stable alternative is to derive the seed from a hash of the ticker.
4. **No session reference price.** The "daily change %" for the watchlist has to come from the frontend's first-seen price, or from an added `session_open` (see `MARKET_INTERFACE.md` §4.4).
5. **Tick drift.** The loop sleeps `interval` *after* each step, so the real period is `0.5s + step time`. That's negligible, and `dt` is a fixed modeling constant anyway.

---

## 6. Testing approach

Existing tests: `tests/market/test_simulator.py` (engine) and `test_simulator_source.py` (async adapter).

- **Engine:** prices stay positive over many steps. `step()` returns every ticker. Add and remove rebuild the Cholesky factor (`None` when n ≤ 1). Unknown tickers get default parameters. The pairwise correlation rules match §2.2.
- **Statistics** (seeded, large N): the sample correlation of log-returns between AAPL and MSFT is ≈ 0.6, and between AAPL and JPM ≈ 0.3, with events turned off (`event_probability=0`). The standard deviation of log-returns per tick is ≈ `σ·√dt`.
- **Events:** with `event_probability=1.0`, every tick moves each ticker by 2–5% beyond the GBM move.
- **Adapter:** `start()` seeds the cache before returning. The loop updates `cache.version`. `add_ticker` makes the price visible immediately. `remove_ticker` evicts the ticker from the cache. `stop()` is idempotent and leaves no running task.
