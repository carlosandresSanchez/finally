import pytest

from app.market import MarketDataSource, PriceCache, create_market_data_source, normalize_ticker
from app.market.factory import DEFAULT_MASSIVE_POLL_INTERVAL, MIN_MASSIVE_POLL_INTERVAL
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.mark.parametrize(
    "raw,expected", [(" aapl ", "AAPL"), ("BRK.B", "BRK.B"), ("brk-b", "BRK-B"), ("v", "V")]
)
def test_normalize_ok(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize("raw", ["", "  ", "123", "AAPL;DROP", "TOOLONGTICKER", "a b"])
def test_normalize_rejects_garbage(raw):
    with pytest.raises(ValueError):
        normalize_ticker(raw)


def test_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        MarketDataSource()  # type: ignore[abstract]


def test_both_sources_implement_interface():
    assert issubclass(SimulatorDataSource, MarketDataSource)
    assert issubclass(MassiveDataSource, MarketDataSource)


def test_factory_defaults_to_simulator(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


def test_factory_blank_key_is_simulator(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "   ")
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


def test_factory_key_selects_massive_default_interval(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    monkeypatch.delenv("MASSIVE_POLL_INTERVAL", raising=False)
    src = create_market_data_source(PriceCache())
    assert isinstance(src, MassiveDataSource)
    assert src._interval == DEFAULT_MASSIVE_POLL_INTERVAL


def test_factory_custom_interval(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "5")
    assert create_market_data_source(PriceCache())._interval == 5.0


def test_factory_interval_clamped(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "0.1")
    assert create_market_data_source(PriceCache())._interval == MIN_MASSIVE_POLL_INTERVAL


def test_factory_invalid_interval_falls_back(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", "k")
    monkeypatch.setenv("MASSIVE_POLL_INTERVAL", "abc")
    assert create_market_data_source(PriceCache())._interval == DEFAULT_MASSIVE_POLL_INTERVAL
