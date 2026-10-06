import asyncio

import pytest
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import (
    MAX_BACKOFF_SECONDS,
    MassiveDataSource,
    parse_snapshot,
    to_unix_seconds,
)

T_NS = 1_707_580_800_123_456_789


def snap(ticker="AAPL", price=190.85, prev_close=189.60, t_ns=T_NS, **extra):
    d = {
        "ticker": ticker,
        "updated": t_ns,
        "prevDay": {"c": prev_close},
        "day": {"c": price},
        "lastTrade": {"p": price, "s": 100, "t": t_ns},
    }
    d.update(extra)
    return TickerSnapshot.from_dict(d)


def make_source(**kw):
    src = MassiveDataSource("k", PriceCache(), **kw)
    src._client = object()
    src._tickers = ["AAPL"]
    return src


def test_parse_real_snapshot():
    q = parse_snapshot(snap())
    assert (q.ticker, q.price, q.session_open) == ("AAPL", 190.85, 189.60)
    assert q.timestamp == pytest.approx(1_707_580_800.123, abs=1e-3)


def test_parse_falls_back_to_minute_bar():
    s = TickerSnapshot.from_dict(
        {"ticker": "AAPL", "min": {"c": 190.1, "t": 1_707_580_740_000}, "prevDay": {"c": 189.6}}
    )
    q = parse_snapshot(s)
    assert q.price == 190.1 and q.session_open == 189.6


def test_parse_falls_back_to_day_close():
    s = TickerSnapshot.from_dict({"ticker": "AAPL", "day": {"c": 190.5}, "prevDay": {"c": 189.6}})
    assert parse_snapshot(s).price == 190.5


def test_parse_falls_back_to_prev_close():
    s = TickerSnapshot.from_dict({"ticker": "AAPL", "day": {"c": 0}, "prevDay": {"c": 189.6}})
    assert parse_snapshot(s).price == 189.6


def test_parse_timestamp_falls_back_to_updated():
    s = TickerSnapshot.from_dict({"ticker": "AAPL", "updated": T_NS, "day": {"c": 1.0}})
    assert parse_snapshot(s).timestamp == pytest.approx(1_707_580_800.123, abs=1e-3)


def test_parse_unusable_returns_none():
    assert parse_snapshot(TickerSnapshot.from_dict({"ticker": "AAPL"})) is None
    assert parse_snapshot(TickerSnapshot.from_dict({"day": {"c": 5}})) is None


def test_parse_uppercases_ticker():
    assert parse_snapshot(snap(ticker="aapl")).ticker == "AAPL"


@pytest.mark.parametrize(
    "raw,expected",
    [
        (1_707_580_800, 1_707_580_800.0),
        (1_707_580_800_000, 1_707_580_800.0),
        (1_707_580_800_000_000, 1_707_580_800.0),
        (1_707_580_800_000_000_000, 1_707_580_800.0),
        (None, None),
        (0, None),
        (-5, None),
    ],
)
def test_to_unix_seconds(raw, expected):
    assert to_unix_seconds(raw) == expected


async def test_poll_once_updates_cache_and_ignores_removed():
    src = make_source(poll_interval=60)
    src._fetch_snapshots = lambda tickers: [snap("AAPL"), snap("GOOGL", 175.0, 174.0)]
    await src._poll_once()
    cache = src._cache
    assert cache.get_price("AAPL") == 190.85
    assert cache.get("AAPL").day_change == 1.25
    assert "GOOGL" not in cache


async def test_poll_once_skips_unusable_and_logs_missing(caplog):
    src = make_source()
    src._tickers = ["AAPL", "MSFT"]
    src._fetch_snapshots = lambda t: [TickerSnapshot.from_dict({"ticker": "AAPL"})]
    await src._poll_once()
    assert len(src._cache) == 0
    assert "no data" in caplog.text


async def test_poll_once_without_tickers_or_client_is_noop():
    src = MassiveDataSource("k", PriceCache())
    await src._poll_once()  # no client
    src._client = object()
    await src._poll_once()  # no tickers
    assert len(src._cache) == 0


async def test_backoff_grows_and_resets():
    src = make_source(poll_interval=15)

    def boom(_):
        raise RuntimeError("429")

    src._fetch_snapshots = boom
    await src._poll_once()
    assert src._next_delay() == 30
    await src._poll_once()
    assert src._next_delay() == 60
    src._fetch_snapshots = lambda _: [snap()]
    await src._poll_once()
    assert src._next_delay() == 15


async def test_backoff_is_capped():
    src = make_source(poll_interval=15)
    src._consecutive_failures = 20
    assert src._next_delay() == MAX_BACKOFF_SECONDS


async def test_failed_poll_keeps_last_prices():
    src = make_source()
    src._fetch_snapshots = lambda _: [snap()]
    await src._poll_once()

    def boom(_):
        raise RuntimeError("down")

    src._fetch_snapshots = boom
    await src._poll_once()
    assert src._cache.get_price("AAPL") == 190.85


async def test_add_remove_ticker_and_get_tickers():
    src = make_source()
    await src.add_ticker("msft")
    await src.add_ticker("MSFT")
    assert src.get_tickers() == ["AAPL", "MSFT"]
    src._cache.update("MSFT", 1.0)
    await src.remove_ticker("msft")
    assert src.get_tickers() == ["AAPL"] and "MSFT" not in src._cache
    src.get_tickers().append("X")  # returned list is a copy
    assert src.get_tickers() == ["AAPL"]


async def test_start_polls_immediately_and_stop_is_idempotent(monkeypatch):
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=60)
    monkeypatch.setattr(src, "_fetch_snapshots", lambda t: [snap(ticker=x) for x in t])
    await src.start(["aapl", "AAPL", "msft"])
    assert src.get_tickers() == ["AAPL", "MSFT"]
    assert cache.get_price("AAPL") == 190.85 and cache.get_price("MSFT") == 190.85
    await src.stop()
    await src.stop()
    assert src._client is None


async def test_poll_loop_polls_repeatedly(monkeypatch):
    cache = PriceCache()
    src = MassiveDataSource("k", cache, poll_interval=0.01)
    calls = []

    def fetch(t):
        calls.append(1)
        return [snap()]

    monkeypatch.setattr(src, "_fetch_snapshots", fetch)
    await src.start(["AAPL"])
    await asyncio.sleep(0.15)
    await src.stop()
    assert len(calls) >= 3


def test_fetch_snapshots_calls_sdk():
    class FakeClient:
        def get_snapshot_all(self, market_type, tickers):
            self.args = (market_type, tickers)
            return ["x"]

    src = MassiveDataSource("k", PriceCache())
    src._client = FakeClient()
    assert src._fetch_snapshots(["AAPL"]) == ["x"]
    assert src._client.args[1] == ["AAPL"]
