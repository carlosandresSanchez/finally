import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource


def snap(ticker, price, ts_ms=1_700_000_000_000):
    return SimpleNamespace(ticker=ticker, last_trade=SimpleNamespace(price=price, timestamp=ts_ms))


@pytest.fixture
def make():
    def _make(interval=15.0):
        cache = PriceCache()
        with patch("app.market.massive_client.RESTClient") as rc:
            src = MassiveDataSource("key", cache, poll_interval=interval)
        return src, cache, rc.return_value

    return _make


def test_client_created_with_key():
    with patch("app.market.massive_client.RESTClient") as rc:
        MassiveDataSource("secret", PriceCache())
    rc.assert_called_once_with(api_key="secret")


def test_is_market_data_source(make):
    src, _, _ = make()
    assert isinstance(src, MarketDataSource)


async def test_poll_once_writes_cache_with_seconds_timestamp(make):
    src, cache, client = make()
    client.get_snapshot_all.return_value = [snap("AAPL", 191.5), snap("MSFT", 420.25)]
    await src.start(["AAPL", "MSFT"])
    try:
        assert cache.get_price("AAPL") == 191.5
        assert cache.get("MSFT").timestamp == 1_700_000_000.0
        kwargs = client.get_snapshot_all.call_args.kwargs
        assert kwargs["tickers"] == ["AAPL", "MSFT"]
        assert "STOCKS" in str(kwargs["market_type"]).upper()
    finally:
        await src.stop()


async def test_second_poll_sets_previous_price(make):
    src, cache, client = make()
    client.get_snapshot_all.return_value = [snap("AAPL", 100.0)]
    await src.start(["AAPL"])
    await src.stop()
    client.get_snapshot_all.return_value = [snap("AAPL", 101.0)]
    await src._poll_once()
    u = cache.get("AAPL")
    assert (u.previous_price, u.direction) == (100.0, "up")


async def test_poll_loop_polls_repeatedly(make):
    src, _, client = make(interval=0.01)
    client.get_snapshot_all.return_value = [snap("AAPL", 1.0)]
    await src.start(["AAPL"])
    await asyncio.sleep(0.1)
    await src.stop()
    assert client.get_snapshot_all.call_count >= 3


async def test_api_error_does_not_crash_or_touch_cache(make):
    src, cache, client = make()
    client.get_snapshot_all.side_effect = RuntimeError("429")
    await src.start(["AAPL"])
    assert len(cache) == 0
    await src.stop()


async def test_loop_survives_error_then_recovers(make):
    src, cache, client = make(interval=0.01)
    client.get_snapshot_all.side_effect = [RuntimeError("boom"), [snap("AAPL", 5.0)]] + [
        [snap("AAPL", 5.0)]
    ] * 50
    await src.start(["AAPL"])
    await asyncio.sleep(0.1)
    await src.stop()
    assert cache.get_price("AAPL") == 5.0


async def test_malformed_snapshot_skipped(make):
    src, cache, client = make()
    bad = SimpleNamespace(ticker="BAD", last_trade=None)
    client.get_snapshot_all.return_value = [bad, snap("AAPL", 10.0)]
    await src.start(["BAD", "AAPL"])
    await src.stop()
    assert "BAD" not in cache and cache.get_price("AAPL") == 10.0


async def test_unrequested_ticker_ignored(make):
    src, cache, client = make()
    client.get_snapshot_all.return_value = [snap("ZZZ", 1.0)]
    await src.start(["AAPL"])
    await src.stop()
    assert "ZZZ" not in cache


async def test_add_remove_ticker(make):
    src, cache, client = make()
    client.get_snapshot_all.return_value = [snap("AAPL", 1.0)]
    await src.start(["AAPL"])
    await src.add_ticker("MSFT")
    await src.add_ticker("MSFT")
    assert src.get_tickers() == ["AAPL", "MSFT"]
    await src.remove_ticker("AAPL")
    assert src.get_tickers() == ["MSFT"] and "AAPL" not in cache
    await src.stop()


async def test_no_tickers_means_no_api_call(make):
    src, _, client = make()
    await src.start([])
    await src.stop()
    client.get_snapshot_all.assert_not_called()


async def test_start_twice_and_stop_idempotent(make):
    src, _, client = make()
    client.get_snapshot_all.return_value = []
    await src.start(["AAPL"])
    task = src._task
    await src.start(["AAPL"])
    assert src._task is task
    await src.stop()
    await src.stop()


async def test_duplicate_tickers_deduped(make):
    src, _, client = make()
    client.get_snapshot_all.return_value = []
    await src.start(["AAPL", "AAPL"])
    await src.stop()
    assert src.get_tickers() == ["AAPL"]
