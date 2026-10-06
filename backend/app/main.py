"""FastAPI application: REST API, SSE stream, background tasks, static frontend."""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager, suppress
from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app.chat import ChatService
from app.config import SNAPSHOT_INTERVAL_SECONDS, db_path, static_dir
from app.db import Database
from app.market import MarketDataSource, PriceCache, create_market_data_source, create_stream_router
from app.services import TradeError, TradingService

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)


class TradeRequest(BaseModel):
    ticker: str
    quantity: float = Field(gt=0)
    side: str


class WatchlistRequest(BaseModel):
    ticker: str


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


def _trading(request: Request) -> TradingService:
    return request.app.state.trading


def _chat(request: Request) -> ChatService:
    return request.app.state.chat


def build_api_router() -> APIRouter:
    api = APIRouter(prefix="/api")

    @api.get("/health")
    async def health(request: Request) -> dict:
        return {"status": "ok", "tickers": len(request.app.state.cache)}

    # ---- portfolio
    @api.get("/portfolio")
    async def get_portfolio(request: Request) -> dict:
        return _trading(request).get_portfolio()

    @api.post("/portfolio/trade")
    async def trade(body: TradeRequest, request: Request) -> dict:
        trading = _trading(request)
        try:
            result = await trading.execute_trade(body.ticker, body.side, body.quantity)
        except TradeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"trade": result.to_dict(), "portfolio": trading.get_portfolio()}

    @api.get("/portfolio/history")
    async def history(request: Request) -> dict:
        return {"snapshots": _trading(request).history()}

    @api.get("/portfolio/trades")
    async def trades(request: Request) -> dict:
        return {"trades": _trading(request).recent_trades()}

    # ---- watchlist
    @api.get("/watchlist")
    async def get_watchlist(request: Request) -> dict:
        return {"watchlist": _trading(request).get_watchlist()}

    @api.post("/watchlist", status_code=201)
    async def add_watchlist(body: WatchlistRequest, request: Request) -> dict:
        try:
            ticker, added = await _trading(request).add_to_watchlist(body.ticker)
        except TradeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not added:
            raise HTTPException(status_code=409, detail=f"{ticker} is already on the watchlist")
        return {"ticker": ticker, "watchlist": _trading(request).get_watchlist()}

    @api.delete("/watchlist/{ticker}")
    async def remove_watchlist(ticker: str, request: Request) -> dict:
        try:
            ticker, removed = await _trading(request).remove_from_watchlist(ticker)
        except TradeError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not removed:
            raise HTTPException(status_code=404, detail=f"{ticker} is not on the watchlist")
        return {"ticker": ticker, "watchlist": _trading(request).get_watchlist()}

    # ---- chat
    @api.post("/chat")
    async def chat(body: ChatRequest, request: Request) -> dict:
        if not body.message.strip():
            raise HTTPException(status_code=422, detail="Message must not be blank")
        return await _chat(request).send(body.message)

    @api.get("/chat/history")
    async def chat_history(request: Request) -> dict:
        return {"messages": _chat(request).history(limit=100)}

    return api


async def _snapshot_loop(trading: TradingService, interval: float) -> None:
    while True:
        await asyncio.sleep(interval)
        try:
            trading.record_snapshot()
        except Exception:
            logger.exception("Portfolio snapshot failed")


def _mount_frontend(app: FastAPI, directory: Path) -> None:
    """Serve the Next.js static export at / (API routes are registered first and win)."""
    if not (directory / "index.html").exists():
        logger.warning("No frontend build at %s; serving API only", directory)
        return

    app.mount("/_next", StaticFiles(directory=directory / "_next"), name="next-assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str) -> FileResponse:
        if path.startswith("api/"):
            raise HTTPException(status_code=404)
        root = directory.resolve()
        candidate = (root / path).resolve()
        if candidate.is_relative_to(root):
            if candidate.is_file():
                return FileResponse(candidate)
            if candidate.with_suffix(".html").is_file():
                return FileResponse(candidate.with_suffix(".html"))
        return FileResponse(root / "index.html")  # SPA fallback


def create_app(
    database: Database | None = None,
    source_factory=create_market_data_source,
    snapshot_interval: float = SNAPSHOT_INTERVAL_SECONDS,
    frontend_dir: Path | None = None,
) -> FastAPI:
    db = database or Database(db_path())
    cache = PriceCache()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db.initialize()
        source: MarketDataSource = source_factory(cache)
        trading = TradingService(db, cache, source)
        app.state.trading = trading
        app.state.chat = ChatService(trading)
        await source.start(trading.tracked_tickers())
        trading.record_snapshot()
        snapshot_task = asyncio.create_task(_snapshot_loop(trading, snapshot_interval))
        try:
            yield
        finally:
            snapshot_task.cancel()
            with suppress(asyncio.CancelledError):
                await snapshot_task
            await source.stop()

    app = FastAPI(title="FinAlly", version="0.1.0", lifespan=lifespan)
    app.state.cache = cache
    app.include_router(create_stream_router(cache))
    app.include_router(build_api_router())
    _mount_frontend(app, frontend_dir or static_dir())
    return app


app = create_app()
