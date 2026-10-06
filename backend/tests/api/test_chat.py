"""Chat: structured-output parsing, mock mode, and action execution."""

from __future__ import annotations

from unittest.mock import patch

from app.chat import ChatLLMResponse, mock_llm_response, parse_llm_response


def test_parse_full_schema():
    r = parse_llm_response(
        '{"message": "Done", "trades": [{"ticker": "AAPL", "side": "buy", "quantity": 3}],'
        ' "watchlist_changes": [{"ticker": "PYPL", "action": "add"}]}'
    )
    assert r.message == "Done"
    assert r.trades[0].quantity == 3
    assert r.watchlist_changes[0].action == "add"


def test_parse_message_only():
    r = parse_llm_response('{"message": "Hi"}')
    assert r.message == "Hi" and r.trades == [] and r.watchlist_changes == []


def test_parse_json_wrapped_in_prose():
    r = parse_llm_response('Sure!\n```json\n{"message": "ok", "trades": []}\n```')
    assert r.message == "ok"


def test_parse_bad_actions_keeps_message():
    r = parse_llm_response('{"message": "ok", "trades": [{"ticker": "AAPL", "side": "hold"}]}')
    assert r.message == "ok" and r.trades == []


def test_parse_plain_text_and_empty():
    assert parse_llm_response("just text").message == "just text"
    assert "try again" in parse_llm_response("").message
    assert "try again" in parse_llm_response(None).message


def test_mock_understands_commands():
    r = mock_llm_response("Please buy 5 AAPL and sell 2 shares of TSLA, then add PYPL")
    assert [(t.side, t.quantity, t.ticker) for t in r.trades] == [
        ("buy", 5, "AAPL"), ("sell", 2, "TSLA")
    ]
    assert [(c.action, c.ticker) for c in r.watchlist_changes] == [("add", "PYPL")]


def test_mock_default_reply():
    r = mock_llm_response("How is my portfolio?")
    assert r.trades == [] and "FinAlly" in r.message


def test_chat_mock_executes_trade(client):
    r = client.post("/api/chat", json={"message": "buy 2 AAPL"})
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "assistant"
    [trade] = body["actions"]["trades"]
    assert trade["status"] == "executed" and trade["ticker"] == "AAPL"
    assert client.get("/api/portfolio").json()["positions"][0]["quantity"] == 2


def test_chat_reports_failed_trade(client):
    body = client.post("/api/chat", json={"message": "sell 5 MSFT"}).json()
    assert body["actions"]["trades"][0]["status"] == "failed"
    assert "Insufficient shares" in body["content"]


def test_chat_watchlist_changes(client):
    body = client.post("/api/chat", json={"message": "add PYPL and remove NFLX"}).json()
    statuses = {c["ticker"]: c["status"] for c in body["actions"]["watchlist_changes"]}
    assert statuses == {"PYPL": "ok", "NFLX": "ok"}
    tickers = [i["ticker"] for i in client.get("/api/watchlist").json()["watchlist"]]
    assert "PYPL" in tickers and "NFLX" not in tickers


def test_chat_history_persisted(client):
    client.post("/api/chat", json={"message": "hello"})
    msgs = client.get("/api/chat/history").json()["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[0]["content"] == "hello" and msgs[0]["actions"] is None


def test_chat_rejects_blank(client):
    assert client.post("/api/chat", json={"message": "   "}).status_code == 422
    assert client.post("/api/chat", json={"message": ""}).status_code == 422


def test_chat_real_mode_uses_llm_and_context(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")
    captured = {}

    async def fake_call(self, messages):
        captured["messages"] = messages
        return ChatLLMResponse(
            message="Bought some NVDA.",
            trades=[{"ticker": "NVDA", "side": "buy", "quantity": 1}],
        )

    with patch("app.chat.ChatService._call_llm", fake_call):
        body = client.post("/api/chat", json={"message": "buy one nvidia"}).json()

    assert body["actions"]["trades"][0]["status"] == "executed"
    context = captured["messages"][1]["content"]
    assert "Cash: $10,000.00" in context and "AAPL" in context
    assert captured["messages"][-1] == {"role": "user", "content": "buy one nvidia"}


def test_chat_llm_failure_is_graceful(client, monkeypatch):
    monkeypatch.setenv("LLM_MOCK", "false")

    async def boom(self, messages):
        raise RuntimeError("network down")

    with patch("app.chat.ChatService._call_llm", boom):
        r = client.post("/api/chat", json={"message": "hi"})
    assert r.status_code == 200
    assert "couldn't reach" in r.json()["content"]
