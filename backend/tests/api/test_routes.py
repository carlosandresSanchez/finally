"""REST API: status codes, response shapes, trading and watchlist behavior."""

from __future__ import annotations

import pytest

from app.config import DEFAULT_WATCHLIST


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"
    assert r.json()["tickers"] == len(DEFAULT_WATCHLIST)


def test_fresh_portfolio(client):
    p = client.get("/api/portfolio").json()
    assert p["cash_balance"] == 10000.0
    assert p["total_value"] == 10000.0
    assert p["positions"] == []


def test_default_watchlist_has_prices(client):
    items = client.get("/api/watchlist").json()["watchlist"]
    assert [i["ticker"] for i in items] == DEFAULT_WATCHLIST
    assert all(i["price"] > 0 for i in items)


def test_buy_then_sell_round_trip(client):
    price = client.get("/api/watchlist").json()["watchlist"][0]["price"]  # AAPL

    r = client.post("/api/portfolio/trade", json={"ticker": "aapl", "quantity": 10, "side": "buy"})
    assert r.status_code == 200
    body = r.json()
    assert body["trade"]["ticker"] == "AAPL"
    assert body["trade"]["price"] == price
    assert body["portfolio"]["cash_balance"] == pytest.approx(10000 - 10 * price, abs=0.01)
    [pos] = body["portfolio"]["positions"]
    assert pos["quantity"] == 10
    assert pos["avg_cost"] == pytest.approx(price)

    r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 4, "side": "sell"})
    assert r.status_code == 200
    assert r.json()["portfolio"]["positions"][0]["quantity"] == 6

    r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 6, "side": "sell"})
    p = r.json()["portfolio"]
    assert p["positions"] == []
    assert p["cash_balance"] == pytest.approx(10000.0, abs=0.01)

    trades = client.get("/api/portfolio/trades").json()["trades"]
    assert len(trades) == 3


def test_fractional_shares(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "MSFT", "quantity": 0.5, "side": "buy"})
    assert r.status_code == 200
    assert r.json()["portfolio"]["positions"][0]["quantity"] == 0.5


def test_insufficient_cash(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "NVDA", "quantity": 1000, "side": "buy"})
    assert r.status_code == 400
    assert "Insufficient cash" in r.json()["detail"]
    assert client.get("/api/portfolio").json()["cash_balance"] == 10000.0


def test_sell_more_than_owned(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    r = client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 2, "side": "sell"})
    assert r.status_code == 400
    assert "Insufficient shares" in r.json()["detail"]


def test_sell_without_position(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "TSLA", "quantity": 1, "side": "sell"})
    assert r.status_code == 400


@pytest.mark.parametrize(
    "payload",
    [
        {"ticker": "AAPL", "quantity": 0, "side": "buy"},
        {"ticker": "AAPL", "quantity": -1, "side": "buy"},
        {"ticker": "AAPL", "quantity": 1, "side": "hold"},
        {"ticker": "!!", "quantity": 1, "side": "buy"},
    ],
)
def test_invalid_trades(client, payload):
    assert client.post("/api/portfolio/trade", json=payload).status_code in (400, 422)


def test_buy_untracked_ticker_starts_tracking(client):
    r = client.post("/api/portfolio/trade", json={"ticker": "PYPL", "quantity": 1, "side": "buy"})
    assert r.status_code == 200
    # Held but not watched: still priced in the portfolio.
    pos = client.get("/api/portfolio").json()["positions"][0]
    assert pos["ticker"] == "PYPL" and pos["current_price"] > 0


def test_weighted_average_cost(client):
    app = client.app
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"})
    first = client.get("/api/portfolio").json()["positions"][0]["avg_cost"]
    app.state.cache.update("AAPL", first + 10)
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 10, "side": "buy"})
    pos = client.get("/api/portfolio").json()["positions"][0]
    assert pos["avg_cost"] == pytest.approx(first + 5, abs=0.01)
    assert pos["unrealized_pnl"] == pytest.approx(100, abs=0.1)


def test_selling_at_a_loss(client):
    app = client.app
    client.post("/api/portfolio/trade", json={"ticker": "JPM", "quantity": 10, "side": "buy"})
    cost = client.get("/api/portfolio").json()["positions"][0]["avg_cost"]
    app.state.cache.update("JPM", cost - 20)
    p = client.get("/api/portfolio").json()
    assert p["positions"][0]["unrealized_pnl"] == pytest.approx(-200, abs=0.1)
    client.post("/api/portfolio/trade", json={"ticker": "JPM", "quantity": 10, "side": "sell"})
    assert client.get("/api/portfolio").json()["cash_balance"] == pytest.approx(9800, abs=0.1)


def test_history_has_snapshots(client):
    client.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 1, "side": "buy"})
    snaps = client.get("/api/portfolio/history").json()["snapshots"]
    assert len(snaps) >= 2  # startup + post-trade
    assert {"total_value", "recorded_at"} <= set(snaps[0])


def test_watchlist_add_remove(client):
    r = client.post("/api/watchlist", json={"ticker": "pypl"})
    assert r.status_code == 201
    assert r.json()["ticker"] == "PYPL"
    tickers = [i["ticker"] for i in client.get("/api/watchlist").json()["watchlist"]]
    assert tickers[-1] == "PYPL"
    assert client.get("/api/watchlist").json()["watchlist"][-1]["price"] > 0

    assert client.post("/api/watchlist", json={"ticker": "PYPL"}).status_code == 409

    assert client.delete("/api/watchlist/PYPL").status_code == 200
    assert "PYPL" not in [i["ticker"] for i in client.get("/api/watchlist").json()["watchlist"]]
    assert "PYPL" not in client.app.state.cache
    assert client.delete("/api/watchlist/PYPL").status_code == 404


def test_invalid_watchlist_ticker(client):
    assert client.post("/api/watchlist", json={"ticker": "bad ticker!"}).status_code == 400


def test_removing_watched_ticker_keeps_held_position_priced(client):
    client.post("/api/portfolio/trade", json={"ticker": "TSLA", "quantity": 1, "side": "buy"})
    client.delete("/api/watchlist/TSLA")
    assert "TSLA" in client.app.state.cache
    client.post("/api/portfolio/trade", json={"ticker": "TSLA", "quantity": 1, "side": "sell"})
    assert "TSLA" not in client.app.state.cache


def test_unknown_api_route_404(client):
    assert client.get("/api/does-not-exist").status_code == 404


def test_state_persists_across_restarts(db, tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import create_app
    from tests.conftest import _source_factory

    def make():
        return create_app(database=db, source_factory=_source_factory, snapshot_interval=3600,
                          frontend_dir=tmp_path / "none")

    with TestClient(make()) as c:
        c.post("/api/portfolio/trade", json={"ticker": "AAPL", "quantity": 2, "side": "buy"})
        c.post("/api/watchlist", json={"ticker": "PYPL"})
    with TestClient(make()) as c:
        assert c.get("/api/portfolio").json()["positions"][0]["quantity"] == 2
        assert "PYPL" in [i["ticker"] for i in c.get("/api/watchlist").json()["watchlist"]]
