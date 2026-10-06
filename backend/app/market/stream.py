"""SSE endpoint streaming prices from the PriceCache."""

import asyncio
import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from .cache import PriceCache


async def price_event_stream(
    price_cache: PriceCache,
    request: Request | None = None,
    interval: float = 0.5,
) -> AsyncIterator[str]:
    """Yield an SSE event with all prices whenever the cache version changes."""
    yield "retry: 1000\n\n"
    last_version = -1
    while True:
        if request is not None and await request.is_disconnected():
            break
        if price_cache.version != last_version:
            last_version = price_cache.version
            prices = price_cache.get_all()
            if prices:
                payload = {t: p.to_dict() for t, p in prices.items()}
                yield f"data: {json.dumps(payload)}\n\n"
        await asyncio.sleep(interval)


def create_stream_router(price_cache: PriceCache, interval: float = 0.5) -> APIRouter:
    router = APIRouter(prefix="/api/stream", tags=["stream"])

    @router.get("/prices")
    async def stream_prices(request: Request) -> StreamingResponse:
        return StreamingResponse(
            price_event_stream(price_cache, request, interval),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    return router
