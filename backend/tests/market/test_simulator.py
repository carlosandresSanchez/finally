import asyncio

import numpy as np

from app.market.cache import PriceCache
from app.market.seed_prices import SEED_PRICES
from app.market.simulator import GBMSimulator, SimulatorDataSource


def test_initial_prices_are_seeds():
    sim = GBMSimulator(["AAPL", "MSFT"], seed=1)
    assert sim.get_price("AAPL") == SEED_PRICES["AAPL"]
    assert sim.get_session_open("MSFT") == SEED_PRICES["MSFT"]


def test_step_returns_positive_rounded_prices():
    sim = GBMSimulator(list(SEED_PRICES), seed=1)
    for _ in range(100):
        for p in sim.step().values():
            assert p > 0 and p == round(p, 2)


def test_empty_simulator_steps_to_empty():
    assert GBMSimulator([]).step() == {}


def test_seeded_simulator_is_deterministic():
    a = GBMSimulator(["AAPL", "TSLA"], seed=7)
    b = GBMSimulator(["AAPL", "TSLA"], seed=7)
    assert [a.step() for _ in range(100)] == [b.step() for _ in range(100)]


def test_cholesky_full_default_set_and_many_unknowns():
    GBMSimulator(list(SEED_PRICES))
    GBMSimulator(list(SEED_PRICES) + [f"ZZ{i}" for i in range(40)])


def test_ticker_normalization_and_dedupe():
    sim = GBMSimulator(["aapl"])
    sim.add_ticker(" AAPL ")
    assert sim.get_tickers() == ["AAPL"]


def test_add_and_remove_ticker():
    sim = GBMSimulator(["AAPL"], seed=2)
    sim.add_ticker("pypl")
    assert "PYPL" in sim.get_tickers()
    assert 50.0 <= sim.get_price("PYPL") <= 300.0
    assert "PYPL" in sim.step()
    sim.remove_ticker("PYPL")
    sim.remove_ticker("PYPL")  # no-op
    assert sim.get_tickers() == ["AAPL"] and sim.get_price("PYPL") is None


def test_single_ticker_has_no_cholesky():
    assert GBMSimulator(["AAPL"])._cholesky is None


def test_tick_volatility_is_realistic():
    sim = GBMSimulator(["AAPL"], event_probability=0.0, seed=1)
    prices = [sim.step()["AAPL"] for _ in range(5000)]
    rets = np.diff(np.log(prices))
    assert 0.00003 < rets.std() < 0.0002  # ~0.22 * sqrt(dt) ~ 6.4e-5


def test_events_fire():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=3)
    p0 = sim.get_price("AAPL")
    p1 = sim.step()["AAPL"]
    assert 0.019 < abs(p1 / p0 - 1) < 0.051


def test_correlation_rules():
    f = GBMSimulator._pairwise_correlation
    assert f("AAPL", "MSFT") == 0.6
    assert f("JPM", "V") == 0.5
    assert f("TSLA", "AAPL") == 0.3
    assert f("AAPL", "JPM") == 0.3
    assert f("ZZZ", "AAPL") == 0.3


def test_tech_stocks_are_positively_correlated():
    sim = GBMSimulator(["AAPL", "MSFT"], event_probability=0.0, seed=5)
    a, m = [], []
    last = {"AAPL": 190.0, "MSFT": 420.0}
    for _ in range(3000):
        p = sim.step()
        a.append(np.log(p["AAPL"] / last["AAPL"]))
        m.append(np.log(p["MSFT"] / last["MSFT"]))
        last = p
    assert np.corrcoef(a, m)[0, 1] > 0.3


# --- SimulatorDataSource ---


async def test_start_seeds_cache_immediately():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10, seed=1)
    await src.start(["AAPL", "GOOGL"])
    assert cache.get_price("AAPL") == 190.0 and cache.get_price("GOOGL") == 175.0
    assert sorted(src.get_tickers()) == ["AAPL", "GOOGL"]
    await src.stop()


async def test_loop_updates_cache_and_inherits_session_open():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    v = cache.version
    await asyncio.sleep(0.1)
    assert cache.version > v
    assert cache.get("AAPL").session_open == 190.0
    await src.stop()


async def test_add_ticker_seeds_cache_with_session_open():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    await src.add_ticker("pypl")
    u = cache.get("PYPL")
    assert u is not None and u.session_open == u.price
    await src.add_ticker("PYPL")  # no-op
    assert src.get_tickers().count("PYPL") == 1
    await src.stop()


async def test_remove_ticker_clears_cache():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10, seed=1)
    await src.start(["AAPL", "MSFT"])
    await src.remove_ticker("msft")
    assert "MSFT" not in cache and src.get_tickers() == ["AAPL"]
    await src.stop()


async def test_stop_is_idempotent_and_halts_writes():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    await src.stop()
    await src.stop()
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version == v


async def test_operations_before_start_are_safe():
    src = SimulatorDataSource(PriceCache())
    await src.add_ticker("AAPL")  # no sim yet -> no-op
    await src.remove_ticker("AAPL")
    assert src.get_tickers() == []


async def test_loop_survives_step_errors():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    real_step = src._sim.step
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_step()

    src._sim.step = flaky
    await asyncio.sleep(0.1)
    assert calls["n"] > 1
    await src.stop()
