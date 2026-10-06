import pytest

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL", raising=False)


def test_default_is_simulator():
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_key_is_simulator(monkeypatch, value):
    monkeypatch.setenv("MASSIVE_API_KEY", value)
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


def test_key_selects_massive(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", " abc ")
    src = create_market_data_source(PriceCache())
    assert isinstance(src, MassiveDataSource)
    assert src._api_key == "abc"
    assert src._interval == 15.0


def test_poll_interval_from_env(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "3")
    assert create_market_data_source(PriceCache())._interval == 3.0


def test_poll_interval_clamped_to_minimum(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "0.01")
    assert create_market_data_source(PriceCache())._interval == 1.0


def test_invalid_poll_interval_uses_default(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "abc")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "fast")
    assert create_market_data_source(PriceCache())._interval == 15.0


def test_source_is_unstarted():
    src = create_market_data_source(PriceCache())
    assert src.get_tickers() == []
