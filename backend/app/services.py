"""Portfolio, trading and watchlist logic shared by the REST API and the LLM chat flow."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Literal

from app.config import DEFAULT_USER_ID
from app.db import Database, new_id, utc_now
from app.market import MarketDataSource, PriceCache, normalize_ticker

logger = logging.getLogger(__name__)

Side = Literal["buy", "sell"]

# Positions smaller than this are treated as fully closed (float dust after sells).
QTY_EPSILON = 1e-9


class TradeError(ValueError):
    """A trade or watchlist request failed validation. Message is user-facing."""


@dataclass(frozen=True)
class TradeResult:
    ticker: str
    side: Side
    quantity: float
    price: float
    total: float
    cash_balance: float
    executed_at: str

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "side": self.side,
            "quantity": self.quantity,
            "price": self.price,
            "total": round(self.total, 2),
            "cash_balance": round(self.cash_balance, 2),
            "executed_at": self.executed_at,
        }


class TradingService:
    """Owns the user's cash, positions, trades, snapshots and watchlist.

    The market data source tracks the union of watchlist tickers and held
    positions, so a position keeps a live price even after its ticker is
    removed from the watchlist.
    """

    def __init__(
        self,
        db: Database,
        cache: PriceCache,
        source: MarketDataSource,
        user_id: str = DEFAULT_USER_ID,
    ) -> None:
        self.db = db
        self.cache = cache
        self.source = source
        self.user_id = user_id

    # ------------------------------------------------------------------ tracking

    def tracked_tickers(self) -> list[str]:
        """Watchlist tickers (in insertion order) followed by any other held tickers."""
        tickers = self.watchlist_tickers()
        for t in self._position_tickers():
            if t not in tickers:
                tickers.append(t)
        return tickers

    async def _sync_tracking(self, ticker: str) -> None:
        """Track `ticker` iff it is watched or held."""
        if ticker in self.watchlist_tickers() or ticker in self._position_tickers():
            await self.source.add_ticker(ticker)
        else:
            await self.source.remove_ticker(ticker)

    # ------------------------------------------------------------------ portfolio

    def cash_balance(self) -> float:
        with self.db.transaction() as conn:
            row = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = ?", (self.user_id,)
            ).fetchone()
        return float(row["cash_balance"])

    def _position_rows(self) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT ticker, quantity, avg_cost, updated_at FROM positions "
                "WHERE user_id = ? ORDER BY ticker",
                (self.user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def _position_tickers(self) -> list[str]:
        return [r["ticker"] for r in self._position_rows()]

    def get_portfolio(self) -> dict:
        cash = self.cash_balance()
        positions = []
        positions_value = 0.0
        cost_basis_total = 0.0
        for row in self._position_rows():
            qty = row["quantity"]
            avg = row["avg_cost"]
            # Fall back to cost if the feed has no price yet (e.g. Massive warming up).
            price = self.cache.get_price(row["ticker"]) or avg
            value = qty * price
            cost = qty * avg
            pnl = value - cost
            positions_value += value
            cost_basis_total += cost
            positions.append(
                {
                    "ticker": row["ticker"],
                    "quantity": qty,
                    "avg_cost": round(avg, 4),
                    "current_price": round(price, 2),
                    "market_value": round(value, 2),
                    "cost_basis": round(cost, 2),
                    "unrealized_pnl": round(pnl, 2),
                    "unrealized_pnl_percent": round(pnl / cost * 100, 2) if cost else 0.0,
                }
            )
        total = cash + positions_value
        for p in positions:
            p["weight"] = round(p["market_value"] / total * 100, 2) if total else 0.0
        pnl_total = positions_value - cost_basis_total
        return {
            "cash_balance": round(cash, 2),
            "positions_value": round(positions_value, 2),
            "total_value": round(total, 2),
            "unrealized_pnl": round(pnl_total, 2),
            "unrealized_pnl_percent": (
                round(pnl_total / cost_basis_total * 100, 2) if cost_basis_total else 0.0
            ),
            "positions": positions,
        }

    def total_value(self) -> float:
        return self.get_portfolio()["total_value"]

    async def execute_trade(self, ticker: str, side: str, quantity: float) -> TradeResult:
        """Market order at the current cached price. Raises TradeError on validation failure."""
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            raise TradeError(str(exc)) from exc
        side = side.strip().lower() if isinstance(side, str) else side
        if side not in ("buy", "sell"):
            raise TradeError(f"Invalid side {side!r}; must be 'buy' or 'sell'")
        try:
            quantity = float(quantity)
        except (TypeError, ValueError) as exc:
            raise TradeError("Quantity must be a number") from exc
        if not (math.isfinite(quantity) and quantity > 0):
            raise TradeError("Quantity must be a positive number")

        price = self.cache.get_price(ticker)
        if price is None:
            # Not tracked yet: start tracking so the trade can be priced (simulator seeds
            # synchronously). Roll back the tracking if we still have no price.
            await self.source.add_ticker(ticker)
            price = self.cache.get_price(ticker)
            if price is None:
                await self._sync_tracking(ticker)
                raise TradeError(f"No price available for {ticker} yet; try again shortly")

        now = utc_now()
        total = quantity * price
        with self.db.transaction() as conn:
            cash = conn.execute(
                "SELECT cash_balance FROM users_profile WHERE id = ?", (self.user_id,)
            ).fetchone()["cash_balance"]
            pos = conn.execute(
                "SELECT quantity, avg_cost FROM positions WHERE user_id = ? AND ticker = ?",
                (self.user_id, ticker),
            ).fetchone()
            held = pos["quantity"] if pos else 0.0

            if side == "buy":
                if total > cash + 1e-6:
                    raise TradeError(
                        f"Insufficient cash: buying {quantity:g} {ticker} costs "
                        f"${total:,.2f} but only ${cash:,.2f} is available"
                    )
                new_qty = held + quantity
                new_avg = ((held * pos["avg_cost"]) if pos else 0.0) + total
                new_avg /= new_qty
                new_cash = cash - total
            else:
                if quantity > held + QTY_EPSILON:
                    raise TradeError(
                        f"Insufficient shares: tried to sell {quantity:g} {ticker} "
                        f"but only {held:g} held"
                    )
                new_qty = held - quantity
                new_avg = pos["avg_cost"]
                new_cash = cash + total

            conn.execute(
                "UPDATE users_profile SET cash_balance = ? WHERE id = ?",
                (new_cash, self.user_id),
            )
            if new_qty <= QTY_EPSILON:
                conn.execute(
                    "DELETE FROM positions WHERE user_id = ? AND ticker = ?",
                    (self.user_id, ticker),
                )
            elif pos:
                conn.execute(
                    "UPDATE positions SET quantity = ?, avg_cost = ?, updated_at = ? "
                    "WHERE user_id = ? AND ticker = ?",
                    (new_qty, new_avg, now, self.user_id, ticker),
                )
            else:
                conn.execute(
                    "INSERT INTO positions (id, user_id, ticker, quantity, avg_cost, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (new_id(), self.user_id, ticker, new_qty, new_avg, now),
                )
            conn.execute(
                "INSERT INTO trades (id, user_id, ticker, side, quantity, price, executed_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (new_id(), self.user_id, ticker, side, quantity, price, now),
            )

        await self._sync_tracking(ticker)
        self.record_snapshot()
        logger.info("Trade: %s %g %s @ %.2f", side, quantity, ticker, price)
        return TradeResult(ticker, side, quantity, price, total, new_cash, now)

    def recent_trades(self, limit: int = 50) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT ticker, side, quantity, price, executed_at FROM trades "
                "WHERE user_id = ? ORDER BY executed_at DESC LIMIT ?",
                (self.user_id, limit),
            ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ snapshots

    def record_snapshot(self) -> float:
        total = self.total_value()
        with self.db.transaction() as conn:
            conn.execute(
                "INSERT INTO portfolio_snapshots (id, user_id, total_value, recorded_at) "
                "VALUES (?, ?, ?, ?)",
                (new_id(), self.user_id, total, utc_now()),
            )
        return total

    def history(self, limit: int = 2000) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT total_value, recorded_at FROM ("
                "  SELECT total_value, recorded_at FROM portfolio_snapshots "
                "  WHERE user_id = ? ORDER BY recorded_at DESC LIMIT ?"
                ") ORDER BY recorded_at ASC",
                (self.user_id, limit),
            ).fetchall()
        return [{"total_value": round(r["total_value"], 2), "recorded_at": r["recorded_at"]}
                for r in rows]

    # ------------------------------------------------------------------ watchlist

    def watchlist_tickers(self) -> list[str]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT ticker FROM watchlist WHERE user_id = ? ORDER BY added_at, rowid",
                (self.user_id,),
            ).fetchall()
        return [r["ticker"] for r in rows]

    def get_watchlist(self) -> list[dict]:
        with self.db.transaction() as conn:
            rows = conn.execute(
                "SELECT ticker, added_at FROM watchlist WHERE user_id = ? "
                "ORDER BY added_at, rowid",
                (self.user_id,),
            ).fetchall()
        items = []
        for r in rows:
            update = self.cache.get(r["ticker"])
            item = {"ticker": r["ticker"], "added_at": r["added_at"]}
            if update:
                item.update(update.to_dict())
            else:
                item.update({"price": None, "previous_price": None, "direction": "flat",
                             "change_percent": 0.0, "day_change_percent": 0.0})
            items.append(item)
        return items

    async def add_to_watchlist(self, ticker: str) -> tuple[str, bool]:
        """Returns (ticker, added). added=False if it was already present."""
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            raise TradeError(str(exc)) from exc
        with self.db.transaction() as conn:
            cur = conn.execute(
                "INSERT OR IGNORE INTO watchlist (id, user_id, ticker, added_at) "
                "VALUES (?, ?, ?, ?)",
                (new_id(), self.user_id, ticker, utc_now()),
            )
            added = cur.rowcount > 0
        await self.source.add_ticker(ticker)
        return ticker, added

    async def remove_from_watchlist(self, ticker: str) -> tuple[str, bool]:
        """Returns (ticker, removed). removed=False if it was not on the watchlist."""
        try:
            ticker = normalize_ticker(ticker)
        except ValueError as exc:
            raise TradeError(str(exc)) from exc
        with self.db.transaction() as conn:
            cur = conn.execute(
                "DELETE FROM watchlist WHERE user_id = ? AND ticker = ?",
                (self.user_id, ticker),
            )
            removed = cur.rowcount > 0
        await self._sync_tracking(ticker)
        return ticker, removed
