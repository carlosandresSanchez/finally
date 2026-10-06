"""AI chat: portfolio-aware prompt, structured LLM output, auto-executed actions."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Literal

from pydantic import BaseModel, Field, ValidationError

from app.config import llm_mock
from app.db import new_id, utc_now
from app.services import TradeError, TradingService

logger = logging.getLogger(__name__)

MODEL = "openrouter/openai/gpt-oss-120b"
EXTRA_BODY = {"provider": {"order": ["cerebras"]}}
HISTORY_LIMIT = 20

SYSTEM_PROMPT = """You are FinAlly, an AI trading assistant inside a simulated trading \
workstation. The user trades a virtual portfolio with fake money; market orders fill \
instantly at the current price with no fees.

Your job:
- Analyze portfolio composition, risk concentration and P&L using the live context provided.
- Suggest trades with brief reasoning.
- Execute trades when the user asks or agrees, by listing them in "trades". They run \
automatically; never claim a trade happened unless you list it.
- Manage the watchlist proactively via "watchlist_changes" (action "add" or "remove").
- Be concise and data-driven: cite prices, quantities, weights and P&L.
- Only trade tickers with a known price (watchlist or held) unless you also add the ticker \
to the watchlist in the same response. Check cash before buying and shares before selling.

Always respond with JSON matching the schema: {"message": str, "trades": \
[{"ticker": str, "side": "buy"|"sell", "quantity": number}], "watchlist_changes": \
[{"ticker": str, "action": "add"|"remove"}]}. Use empty arrays when there is nothing to do."""


class TradeAction(BaseModel):
    ticker: str
    side: Literal["buy", "sell"]
    quantity: float


class WatchlistChange(BaseModel):
    ticker: str
    action: Literal["add", "remove"]


class ChatLLMResponse(BaseModel):
    message: str
    trades: list[TradeAction] = Field(default_factory=list)
    watchlist_changes: list[WatchlistChange] = Field(default_factory=list)


# ---------------------------------------------------------------------- parsing


def parse_llm_response(raw: str | None) -> ChatLLMResponse:
    """Parse model output into ChatLLMResponse, degrading gracefully.

    Strict JSON first; then the outermost {...} block (models sometimes wrap JSON in
    prose or code fences); finally treat the whole text as a plain message.
    """
    text = (raw or "").strip()
    if not text:
        return ChatLLMResponse(message="Sorry, I didn't get a response. Please try again.")
    try:
        return ChatLLMResponse.model_validate_json(text)
    except ValidationError:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        try:
            return ChatLLMResponse.model_validate_json(text[start : end + 1])
        except ValidationError:
            # Salvage the message even if the action arrays are malformed.
            try:
                data = json.loads(text[start : end + 1])
                if isinstance(data, dict) and isinstance(data.get("message"), str):
                    return ChatLLMResponse(message=data["message"])
            except json.JSONDecodeError:
                pass
    return ChatLLMResponse(message=text)


# ---------------------------------------------------------------------- mock mode

_MOCK_TRADE_RE = re.compile(
    r"\b(buy|sell)\s+(\d+(?:\.\d+)?)\s+(?:shares?\s+of\s+)?([A-Za-z.]{1,10})\b", re.IGNORECASE
)
_MOCK_WATCH_RE = re.compile(r"\b(add|remove|watch|unwatch)\s+([A-Za-z.]{1,10})\b", re.IGNORECASE)


def mock_llm_response(user_message: str) -> ChatLLMResponse:
    """Deterministic stand-in for the LLM (LLM_MOCK=true).

    Understands "buy 5 AAPL", "sell 2 shares of TSLA", "add PYPL", "remove NFLX".
    """
    trades = [
        TradeAction(ticker=m.group(3).upper(), side=m.group(1).lower(), quantity=float(m.group(2)))
        for m in _MOCK_TRADE_RE.finditer(user_message)
    ]
    changes = [
        WatchlistChange(
            ticker=m.group(2).upper(),
            action="add" if m.group(1).lower() in ("add", "watch") else "remove",
        )
        for m in _MOCK_WATCH_RE.finditer(user_message)
        if m.group(2).lower() not in ("to", "from", "the")
    ]
    if trades or changes:
        parts = [f"{t.side} {t.quantity:g} {t.ticker}" for t in trades]
        parts += [f"{c.action} {c.ticker}" for c in changes]
        message = "Mock mode: executing " + ", ".join(parts) + "."
    else:
        message = (
            "Mock mode: I'm FinAlly, your AI trading assistant. "
            "Try 'buy 5 AAPL' or 'add PYPL to my watchlist'."
        )
    return ChatLLMResponse(message=message, trades=trades, watchlist_changes=changes)


# ---------------------------------------------------------------------- service


class ChatService:
    def __init__(self, trading: TradingService) -> None:
        self.trading = trading

    def build_context(self) -> str:
        p = self.trading.get_portfolio()
        lines = [
            f"Cash: ${p['cash_balance']:,.2f}",
            f"Total portfolio value: ${p['total_value']:,.2f}",
            f"Unrealized P&L: ${p['unrealized_pnl']:,.2f} ({p['unrealized_pnl_percent']:.2f}%)",
            "Positions:",
        ]
        if p["positions"]:
            for pos in p["positions"]:
                lines.append(
                    f"  {pos['ticker']}: {pos['quantity']:g} sh @ avg ${pos['avg_cost']:,.2f}, "
                    f"now ${pos['current_price']:,.2f}, value ${pos['market_value']:,.2f} "
                    f"({pos['weight']:.1f}% of portfolio), P&L ${pos['unrealized_pnl']:,.2f} "
                    f"({pos['unrealized_pnl_percent']:.2f}%)"
                )
        else:
            lines.append("  (none)")
        lines.append("Watchlist (live prices):")
        for w in self.trading.get_watchlist():
            price = f"${w['price']:,.2f}" if w.get("price") is not None else "n/a"
            lines.append(f"  {w['ticker']}: {price} (day {w.get('day_change_percent', 0):+.2f}%)")
        return "\n".join(lines)

    def history(self, limit: int = HISTORY_LIMIT) -> list[dict]:
        with self.trading.db.transaction() as conn:
            rows = conn.execute(
                "SELECT id, role, content, actions, created_at FROM ("
                "  SELECT id, role, content, actions, created_at, rowid AS rid "
                "  FROM chat_messages WHERE user_id = ? ORDER BY created_at DESC, rid DESC "
                "  LIMIT ?"
                ") ORDER BY created_at ASC, rid ASC",
                (self.trading.user_id, limit),
            ).fetchall()
        return [
            {**dict(r), "actions": json.loads(r["actions"]) if r["actions"] else None}
            for r in rows
        ]

    def _store(self, role: str, content: str, actions: dict | None = None) -> dict:
        msg = {
            "id": new_id(),
            "role": role,
            "content": content,
            "actions": actions,
            "created_at": utc_now(),
        }
        with self.trading.db.transaction() as conn:
            conn.execute(
                "INSERT INTO chat_messages (id, user_id, role, content, actions, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (msg["id"], self.trading.user_id, role, content,
                 json.dumps(actions) if actions is not None else None, msg["created_at"]),
            )
        return msg

    def _build_messages(self, user_message: str) -> list[dict]:
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "system", "content": "Current portfolio context:\n" + self.build_context()},
        ]
        for m in self.history():
            content = m["content"]
            if m["role"] == "assistant" and m["actions"]:
                content += "\n[Executed actions: " + json.dumps(m["actions"]) + "]"
            messages.append({"role": m["role"], "content": content})
        messages.append({"role": "user", "content": user_message})
        return messages

    async def _call_llm(self, messages: list[dict]) -> ChatLLMResponse:
        from litellm import completion  # heavy import; only needed for real calls

        def _call() -> str | None:
            response = completion(
                model=MODEL,
                messages=messages,
                response_format=ChatLLMResponse,
                reasoning_effort="low",
                extra_body=EXTRA_BODY,
            )
            return response.choices[0].message.content

        return parse_llm_response(await asyncio.to_thread(_call))

    async def execute_actions(self, response: ChatLLMResponse) -> dict:
        """Apply watchlist changes, then trades. Failures are reported, never raised."""
        watch_results = []
        for change in response.watchlist_changes:
            entry = {"ticker": change.ticker.upper(), "action": change.action}
            try:
                if change.action == "add":
                    ticker, ok = await self.trading.add_to_watchlist(change.ticker)
                else:
                    ticker, ok = await self.trading.remove_from_watchlist(change.ticker)
                entry.update(ticker=ticker, status="ok" if ok else "noop")
            except TradeError as exc:
                entry.update(status="failed", error=str(exc))
            watch_results.append(entry)

        trade_results = []
        for trade in response.trades:
            entry = {"ticker": trade.ticker.upper(), "side": trade.side, "quantity": trade.quantity}
            try:
                result = await self.trading.execute_trade(trade.ticker, trade.side, trade.quantity)
                entry.update(result.to_dict(), status="executed")
            except TradeError as exc:
                entry.update(status="failed", error=str(exc))
            trade_results.append(entry)

        return {"trades": trade_results, "watchlist_changes": watch_results}

    async def send(self, user_message: str) -> dict:
        user_message = user_message.strip()
        messages = self._build_messages(user_message)
        self._store("user", user_message)

        if llm_mock():
            response = mock_llm_response(user_message)
        else:
            try:
                response = await self._call_llm(messages)
            except Exception as exc:  # network, auth, provider errors
                logger.exception("LLM call failed")
                response = ChatLLMResponse(
                    message=f"Sorry, I couldn't reach the AI service ({type(exc).__name__}). "
                    "Please check OPENROUTER_API_KEY and try again."
                )

        actions = await self.execute_actions(response)
        content = response.message
        failures = [a for a in actions["trades"] + actions["watchlist_changes"]
                    if a.get("status") == "failed"]
        if failures:
            content += "\n\n" + "\n".join(
                f"⚠️ {a['ticker']}: {a['error']}" for a in failures
            )
        has_actions = actions["trades"] or actions["watchlist_changes"]
        return self._store("assistant", content, actions if has_actions else None)
