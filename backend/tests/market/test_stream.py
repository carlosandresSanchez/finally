import json

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.market import PriceCache, create_stream_router
from app.market.stream import generate_price_events


class FakeRequest:
    def __init__(self, polls: int):
        self._left = polls
        self.client = None

    async def is_disconnected(self):
        self._left -= 1
        return self._left < 0


async def collect(cache, polls, **kw):
    return [f async for f in generate_price_events(cache, FakeRequest(polls), interval=0, **kw)]


async def test_emits_retry_then_data_then_stops():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = await collect(cache, 2)
    assert frames[0] == "retry: 1000\n\n"
    assert frames[1].startswith("id: 1\ndata: ")
    body = json.loads(frames[1].split("data: ", 1)[1])
    assert body["AAPL"]["price"] == 190.0
    assert body["AAPL"]["direction"] == "flat"
    assert len(frames) == 2  # version unchanged: no duplicate


async def test_heartbeat_when_idle():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = await collect(cache, 3, heartbeat=0)
    assert ": ping\n\n" in frames


async def test_no_heartbeat_before_interval():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = await collect(cache, 3, heartbeat=1000)
    assert ": ping\n\n" not in frames


async def test_empty_cache_sends_empty_map():
    frames = await collect(PriceCache(), 1)
    assert frames[1] == "id: 0\ndata: {}\n\n"


async def test_new_event_after_version_change():
    cache = PriceCache()
    cache.update("AAPL", 190.0)

    class Req(FakeRequest):
        async def is_disconnected(self):
            if self._left == 2:
                cache.update("AAPL", 191.0)
            return await super().is_disconnected()

    frames = [f async for f in generate_price_events(cache, Req(3), interval=0)]
    data = [f for f in frames if "data:" in f]
    assert len(data) == 2
    assert json.loads(data[1].split("data: ", 1)[1])["AAPL"]["direction"] == "up"


async def test_removed_ticker_disappears_from_stream():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    cache.update("MSFT", 420.0)

    class Req(FakeRequest):
        async def is_disconnected(self):
            if self._left == 2:
                cache.remove("MSFT")
            return await super().is_disconnected()

    frames = [f async for f in generate_price_events(cache, Req(3), interval=0)]
    last = json.loads([f for f in frames if "data:" in f][-1].split("data: ", 1)[1])
    assert set(last) == {"AAPL"}


def test_create_stream_router_returns_fresh_router_each_call():
    c = PriceCache()
    r1, r2 = create_stream_router(c), create_stream_router(c)
    assert r1 is not r2
    assert len(r1.routes) == len(r2.routes) == 1


def test_route_registered_at_expected_path():
    router = create_stream_router(PriceCache())
    assert router.routes[0].path == "/api/stream/prices"


def test_endpoint_headers_and_first_frames():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    app = FastAPI()
    app.include_router(create_stream_router(cache, interval=0.01))
    with TestClient(app) as client:
        with client.stream("GET", "/api/stream/prices") as resp:
            assert resp.status_code == 200
            assert resp.headers["content-type"].startswith("text/event-stream")
            assert resp.headers["cache-control"] == "no-cache"
            assert resp.headers["x-accel-buffering"] == "no"
            lines = []
            for line in resp.iter_lines():
                lines.append(line)
                if line.startswith("data:"):
                    break
    assert lines[0] == "retry: 1000"
    payload = json.loads(lines[-1][len("data: "):])
    assert payload["AAPL"]["price"] == 190.0
