"""Massive client tests. Fixtures use the SDK's real TickerSnapshot model, not MagicMock,
so attribute-name mistakes (e.g. last_trade.timestamp) are caught."""

import asyncio

import pytest
from massive.rest.models import TickerSnapshot

from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource, parse_snapshot, to_unix_seconds

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


def make_source(tickers=("AAPL",), interval=15):
    src = MassiveDataSource("k", PriceCache(), poll_interval=interval)
    src._client = object()
    src._tickers = list(tickers)
    return src


# --- parsing ---


def test_parse_real_snapshot():
    q = parse_snapshot(snap())
    assert (q.ticker, q.price, q.session_open) == ("AAPL", 190.85, 189.60)
    assert q.timestamp == pytest.approx(1_707_580_800.123, abs=1e-3)


def test_parse_uppercases_ticker():
    assert parse_snapshot(snap("aapl")).ticker == "AAPL"


def test_parse_falls_back_when_no_last_trade():
    s = TickerSnapshot.from_dict({"ticker": "AAPL", "day": {"c": 0}, "prevDay": {"c": 189.6}})
    q = parse_snapshot(s)
    assert q.price == 189.6
    assert q.session_open == 189.6


def test_parse_prefers_minute_bar_over_day():
    s = TickerSnapshot.from_dict(
        {"ticker": "AAPL", "min": {"c": 191.0}, "day": {"c": 190.0}, "prevDay": {"c": 189.0}}
    )
    assert parse_snapshot(s).price == 191.0


def test_parse_timestamp_falls_back_to_updated():
    s = TickerSnapshot.from_dict({"ticker": "AAPL", "updated": T_NS, "prevDay": {"c": 1.0}})
    assert parse_snapshot(s).timestamp == pytest.approx(1_707_580_800.123, abs=1e-3)


def test_parse_timestamp_falls_back_to_now():
    s = TickerSnapshot.from_dict({"ticker": "AAPL", "prevDay": {"c": 1.0}})
    assert parse_snapshot(s).timestamp > 1e9


def test_parse_unusable_returns_none():
    assert parse_snapshot(TickerSnapshot.from_dict({"ticker": "AAPL"})) is None


def test_parse_missing_ticker_returns_none():
    assert parse_snapshot(TickerSnapshot.from_dict({"day": {"c": 1.0}})) is None


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


# --- polling ---


async def test_poll_once_updates_cache_and_ignores_removed():
    src = make_source(["AAPL"])  # GOOGL was removed mid-flight
    src._fetch_snapshots = lambda tickers: [snap("AAPL"), snap("GOOGL", 175.0, 174.0)]
    await src._poll_once()
    cache = src._cache
    assert cache.get_price("AAPL") == 190.85
    assert cache.get("AAPL").day_change == 1.25
    assert "GOOGL" not in cache


async def test_poll_passes_copy_of_tickers():
    src = make_source(["AAPL", "MSFT"])
    seen = []
    src._fetch_snapshots = lambda tickers: seen.append(tickers) or []
    await src._poll_once()
    assert seen == [["AAPL", "MSFT"]]
    assert seen[0] is not src._tickers


async def test_poll_skips_unusable_snapshot():
    src = make_source(["AAPL", "MSFT"])
    bad = TickerSnapshot.from_dict({"ticker": "MSFT"})
    src._fetch_snapshots = lambda tickers: [bad, snap("AAPL")]
    await src._poll_once()
    assert "AAPL" in src._cache
    assert "MSFT" not in src._cache


async def test_poll_noop_without_tickers_or_client():
    src = make_source([])
    called = []
    src._fetch_snapshots = lambda t: called.append(t) or []
    await src._poll_once()
    src._tickers = ["AAPL"]
    src._client = None
    await src._poll_once()
    assert called == []


async def test_poll_timestamp_is_seconds():
    src = make_source()
    src._fetch_snapshots = lambda t: [snap()]
    await src._poll_once()
    assert src._cache.get("AAPL").timestamp == pytest.approx(1_707_580_800.123, abs=1e-3)


async def test_backoff_grows_and_resets():
    src = make_source(interval=15)

    def boom(_):
        raise RuntimeError("429")

    src._fetch_snapshots = boom
    assert src._next_delay() == 15
    await src._poll_once()
    assert src._next_delay() == 30
    await src._poll_once()
    assert src._next_delay() == 60
    src._fetch_snapshots = lambda _: [snap()]
    await src._poll_once()
    assert src._next_delay() == 15


async def test_backoff_capped():
    src = make_source(interval=15)
    src._consecutive_failures = 20
    assert src._next_delay() == 120


async def test_failed_poll_keeps_last_prices():
    src = make_source()
    src._fetch_snapshots = lambda _: [snap()]
    await src._poll_once()

    def boom(_):
        raise RuntimeError("down")

    src._fetch_snapshots = boom
    await src._poll_once()
    assert src._cache.get_price("AAPL") == 190.85


# --- lifecycle ---


async def test_add_remove_tickers():
    src = make_source(["AAPL"])
    await src.add_ticker(" msft ")
    await src.add_ticker("MSFT")
    assert src.get_tickers() == ["AAPL", "MSFT"]
    src._cache.update("MSFT", 1.0)
    await src.remove_ticker("msft")
    assert src.get_tickers() == ["AAPL"]
    assert "MSFT" not in src._cache


async def test_get_tickers_returns_copy():
    src = make_source(["AAPL"])
    src.get_tickers().append("X")
    assert src.get_tickers() == ["AAPL"]


async def test_start_polls_immediately_and_stop_cancels(monkeypatch):
    class FakeClient:
        def __init__(self, api_key):
            self.api_key = api_key

        def get_snapshot_all(self, market_type, tickers):
            return [snap(t) for t in tickers]

    monkeypatch.setattr("app.market.massive_client.RESTClient", FakeClient)
    cache = PriceCache()
    src = MassiveDataSource("key", cache, poll_interval=60)
    await src.start(["aapl", "AAPL", "msft"])
    assert src.get_tickers() == ["AAPL", "MSFT"]
    assert cache.get_price("AAPL") == 190.85
    assert cache.get_price("MSFT") == 190.85
    task = src._task
    await src.stop()
    await src.stop()  # idempotent
    assert task.done()
    assert src._client is None


async def test_poll_loop_polls_repeatedly(monkeypatch):
    calls = []

    class FakeClient:
        def __init__(self, api_key):
            pass

        def get_snapshot_all(self, market_type, tickers):
            calls.append(tickers)
            return [snap("AAPL")]

    monkeypatch.setattr("app.market.massive_client.RESTClient", FakeClient)
    src = MassiveDataSource("key", PriceCache(), poll_interval=0.01)
    await src.start(["AAPL"])
    await asyncio.sleep(0.1)
    await src.stop()
    assert len(calls) >= 3
