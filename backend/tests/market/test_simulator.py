import asyncio
import math

import numpy as np
import pytest

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS
from app.market.simulator import DEFAULT_DT, GBMSimulator, SimulatorDataSource

DEFAULTS = list(SEED_PRICES)


def test_dt_value():
    assert DEFAULT_DT == pytest.approx(8.5e-8, rel=0.02)


def test_seed_prices_used():
    sim = GBMSimulator(DEFAULTS)
    for t, p in SEED_PRICES.items():
        assert sim.get_price(t) == p


def test_unknown_ticker_random_seed_in_range():
    sim = GBMSimulator(["ZZZZ"])
    assert 50 <= sim.get_price("ZZZZ") <= 300


def test_step_returns_all_tickers_positive_rounded():
    sim = GBMSimulator(DEFAULTS)
    for _ in range(100):
        out = sim.step()
        assert set(out) == set(DEFAULTS)
        for p in out.values():
            assert p > 0
            assert p == round(p, 2)


def test_empty_step():
    assert GBMSimulator([]).step() == {}


def test_add_remove_ticker():
    sim = GBMSimulator(["AAPL"])
    sim.add_ticker("MSFT")
    sim.add_ticker("MSFT")  # duplicate ignored
    assert sim.get_tickers() == ["AAPL", "MSFT"]
    assert set(sim.step()) == {"AAPL", "MSFT"}
    sim.remove_ticker("AAPL")
    sim.remove_ticker("nope")
    assert sim.get_tickers() == ["MSFT"]
    assert sim._cholesky is None
    assert sim.get_price("AAPL") is None


def test_gbm_math_single_step_deterministic(monkeypatch):
    sim = GBMSimulator(["AAPL"], event_probability=0.0)
    monkeypatch.setattr(np.random, "standard_normal", lambda n: np.array([1.0]))
    p = TICKER_PARAMS["AAPL"]
    expected = 190.0 * math.exp(
        (p["mu"] - 0.5 * p["sigma"] ** 2) * DEFAULT_DT + p["sigma"] * math.sqrt(DEFAULT_DT)
    )
    assert sim.step()["AAPL"] == round(expected, 2)


def test_log_return_statistics():
    """Per-step log-return std should match sigma*sqrt(dt) (large dt for signal)."""
    np.random.seed(1)
    dt = 1e-4
    sim = GBMSimulator(["MSFT"], dt=dt, event_probability=0.0)
    prices = [sim.get_price("MSFT")]
    for _ in range(5000):
        sim.step()
        prices.append(sim._prices["MSFT"])  # unrounded
    rets = np.diff(np.log(prices))
    assert rets.std() == pytest.approx(TICKER_PARAMS["MSFT"]["sigma"] * math.sqrt(dt), rel=0.1)


def test_correlation_values():
    c = GBMSimulator.correlation
    assert c("AAPL", "MSFT") == 0.6
    assert c("JPM", "V") == 0.5
    assert c("TSLA", "AAPL") == 0.3
    assert c("AAPL", "JPM") == 0.3
    assert c("FOO", "BAR") == 0.3


def test_cholesky_reconstructs_correlation():
    sim = GBMSimulator(DEFAULTS)
    L = sim._cholesky
    corr = L @ L.T
    assert np.allclose(np.diag(corr), 1.0)
    i, j = sim._tickers.index("AAPL"), sim._tickers.index("MSFT")
    assert corr[i, j] == pytest.approx(0.6)


def test_correlated_moves_empirical():
    np.random.seed(7)
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], dt=1e-4, event_probability=0.0)
    last = dict(sim._prices)
    a, m = [], []
    for _ in range(4000):
        sim.step()
        a.append(math.log(sim._prices["AAPL"] / last["AAPL"]))
        m.append(math.log(sim._prices["MSFT"] / last["MSFT"]))
        last = dict(sim._prices)
    assert np.corrcoef(a, m)[0, 1] > 0.4


def test_events_happen_when_probability_one(monkeypatch):
    sim = GBMSimulator(["AAPL"], event_probability=1.0)
    monkeypatch.setattr(np.random, "standard_normal", lambda n: np.array([0.0]))
    before = sim.get_price("AAPL")
    sim.step()
    move = abs(sim.get_price("AAPL") / before - 1)
    assert 0.019 <= move <= 0.051


def test_no_events_when_probability_zero(monkeypatch):
    sim = GBMSimulator(["AAPL"], event_probability=0.0)
    for _ in range(500):
        before = sim.get_price("AAPL")
        sim.step()
        assert abs(sim.get_price("AAPL") / before - 1) < 0.01


# ---- SimulatorDataSource ----


def test_is_market_data_source():
    assert isinstance(SimulatorDataSource(PriceCache()), MarketDataSource)


async def test_start_seeds_cache_and_streams():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL", "GOOGL"])
    assert cache.get_price("AAPL") == 190.0
    assert src.get_tickers() == ["AAPL", "GOOGL"]
    v = cache.version
    await asyncio.sleep(0.1)
    assert cache.version > v
    await src.stop()


async def test_stop_halts_updates_and_is_idempotent():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL"])
    await src.stop()
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version == v
    await src.stop()


async def test_start_twice_is_noop():
    src = SimulatorDataSource(PriceCache(), update_interval=0.01)
    await src.start(["AAPL"])
    task = src._task
    await src.start(["MSFT"])
    assert src._task is task and src.get_tickers() == ["AAPL"]
    await src.stop()


async def test_add_remove_ticker():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start(["AAPL"])
    await src.add_ticker("PYPL")
    assert "PYPL" in cache and "PYPL" in src.get_tickers()
    await src.remove_ticker("PYPL")
    assert "PYPL" not in cache and "PYPL" not in src.get_tickers()
    await src.stop()


async def test_operations_before_start_are_safe():
    src = SimulatorDataSource(PriceCache())
    await src.add_ticker("AAPL")
    await src.remove_ticker("AAPL")
    assert src.get_tickers() == []
