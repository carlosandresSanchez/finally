import json

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


async def test_sse_emits_retry_then_data_then_stops():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = await collect(cache, 2)
    assert frames[0] == "retry: 1000\n\n"
    assert frames[1].startswith("id: 1\ndata: ")
    assert json.loads(frames[1].split("data: ", 1)[1])["AAPL"]["price"] == 190.0
    assert len(frames) == 2  # version unchanged -> no duplicate


async def test_sse_heartbeat_when_idle():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    frames = await collect(cache, 3, heartbeat=0)
    assert ": ping\n\n" in frames


async def test_sse_resends_on_version_change():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    gen = generate_price_events(cache, FakeRequest(5), interval=0)
    frames = [await gen.__anext__(), await gen.__anext__()]
    cache.update("AAPL", 191.0)
    frames.append(await gen.__anext__())
    assert json.loads(frames[2].split("data: ", 1)[1])["AAPL"]["direction"] == "up"
    await gen.aclose()


async def test_sse_empty_cache_sends_empty_map():
    frames = await collect(PriceCache(), 2)
    assert frames[1].endswith("data: {}\n\n")


async def test_sse_removed_ticker_disappears():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    gen = generate_price_events(cache, FakeRequest(5), interval=0)
    await gen.__anext__()
    await gen.__anext__()
    cache.remove("AAPL")
    frame = await gen.__anext__()
    assert json.loads(frame.split("data: ", 1)[1]) == {}
    await gen.aclose()


def test_create_stream_router_is_idempotent():
    c = PriceCache()
    r1, r2 = create_stream_router(c), create_stream_router(c)
    assert r1 is not r2 and len(r1.routes) == len(r2.routes) == 1


def test_route_registered_with_expected_path():
    r = create_stream_router(PriceCache())
    assert r.routes[0].path == "/api/stream/prices"


async def test_endpoint_returns_event_stream_response():
    cache = PriceCache()
    cache.update("AAPL", 190.0)
    router = create_stream_router(cache, interval=0)
    resp = await router.routes[0].endpoint(FakeRequest(2))
    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["x-accel-buffering"] == "no"
    body = [chunk async for chunk in resp.body_iterator]
    assert body[0] == "retry: 1000\n\n" and '"AAPL"' in body[1]
