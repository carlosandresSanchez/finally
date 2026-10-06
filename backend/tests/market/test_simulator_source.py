import asyncio

from app.market.cache import PriceCache
from app.market.simulator import SimulatorDataSource


async def test_start_seeds_cache_immediately():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=10, seed=1)
    await src.start(["AAPL", "MSFT"])
    try:
        assert cache.get_price("AAPL") == 190.0
        assert cache.get("MSFT").session_open == 420.0
        assert src.get_tickers() == ["AAPL", "MSFT"]
    finally:
        await src.stop()


async def test_loop_updates_prices_and_keeps_session_open():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    v0 = cache.version
    await asyncio.sleep(0.1)
    assert cache.version > v0
    assert cache.get("AAPL").session_open == 190.0
    await src.stop()


async def test_add_ticker_seeds_cache_with_session_open():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    await src.add_ticker("pypl")
    u = cache.get("PYPL")
    assert u is not None and u.session_open == u.price
    assert "PYPL" in src.get_tickers()
    await src.stop()


async def test_add_duplicate_is_noop():
    src = SimulatorDataSource(PriceCache(), update_interval=10)
    await src.start(["AAPL"])
    await src.add_ticker("aapl")
    assert src.get_tickers() == ["AAPL"]
    await src.stop()


async def test_add_before_start_is_noop():
    src = SimulatorDataSource(PriceCache())
    await src.add_ticker("AAPL")
    assert src.get_tickers() == []


async def test_remove_ticker_clears_cache():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL", "MSFT"])
    await src.remove_ticker("msft")
    await asyncio.sleep(0.05)
    assert "MSFT" not in cache
    assert src.get_tickers() == ["AAPL"]
    await src.stop()


async def test_remove_before_start_is_safe():
    src = SimulatorDataSource(PriceCache())
    await src.remove_ticker("AAPL")


async def test_stop_is_idempotent_and_halts_writes():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    await src.stop()
    await src.stop()
    v = cache.version
    await asyncio.sleep(0.05)
    assert cache.version == v


async def test_stop_without_start():
    await SimulatorDataSource(PriceCache()).stop()


async def test_start_with_empty_watchlist():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01)
    await src.start([])
    await asyncio.sleep(0.03)
    assert len(cache) == 0
    await src.stop()


async def test_loop_survives_step_exception():
    cache = PriceCache()
    src = SimulatorDataSource(cache, update_interval=0.01, seed=1)
    await src.start(["AAPL"])
    calls = {"n": 0}
    real_step = src._sim.step

    def flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return real_step()

    src._sim.step = flaky
    v = cache.version
    await asyncio.sleep(0.1)
    assert calls["n"] > 1
    assert cache.version > v
    await src.stop()
