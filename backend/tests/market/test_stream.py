import asyncio
import json

from fastapi import FastAPI

from app.market.cache import PriceCache
from app.market.stream import create_stream_router, price_event_stream


async def collect(gen, n):
    out = []
    async for item in gen:
        out.append(item)
        if len(out) == n:
            break
    await gen.aclose()
    return out


async def test_stream_emits_retry_then_data():
    cache = PriceCache()
    cache.update("AAPL", 190.0, timestamp=1.0)
    events = await collect(price_event_stream(cache, interval=0.01), 2)
    assert events[0] == "retry: 1000\n\n"
    assert events[1].startswith("data: ") and events[1].endswith("\n\n")
    payload = json.loads(events[1][6:])
    assert payload["AAPL"] == {
        "ticker": "AAPL",
        "price": 190.0,
        "previous_price": 190.0,
        "timestamp": 1.0,
        "change": 0.0,
        "direction": "flat",
    }


async def test_stream_only_emits_on_change():
    cache = PriceCache()
    cache.update("AAPL", 1.0)
    gen = price_event_stream(cache, interval=0.01)
    await gen.__anext__()  # retry
    await gen.__anext__()  # first data
    nxt = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.1)
    assert not nxt.done()  # nothing changed -> no event
    cache.update("AAPL", 2.0)
    event = await asyncio.wait_for(nxt, 1)
    assert json.loads(event[6:])["AAPL"]["direction"] == "up"
    await gen.aclose()


async def test_stream_skips_empty_cache():
    cache = PriceCache()
    gen = price_event_stream(cache, interval=0.01)
    await gen.__anext__()
    nxt = asyncio.ensure_future(gen.__anext__())
    await asyncio.sleep(0.05)
    assert not nxt.done()
    cache.update("A", 1.0)
    assert (await asyncio.wait_for(nxt, 1)).startswith("data: ")
    await gen.aclose()


async def test_stream_stops_on_disconnect():
    class Req:
        async def is_disconnected(self):
            return True

    cache = PriceCache()
    cache.update("A", 1.0)
    events = [e async for e in price_event_stream(cache, Req(), 0.01)]
    assert events == ["retry: 1000\n\n"]


def test_router_route_registered():
    app = FastAPI()
    app.include_router(create_stream_router(PriceCache()))
    paths = {r.path for r in app.routes}
    assert "/api/stream/prices" in paths
