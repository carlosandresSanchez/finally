import pytest

from app.market import MarketDataSource, normalize_ticker
from app.market.cache import PriceCache
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


@pytest.mark.parametrize(
    "raw,expected",
    [("AAPL", "AAPL"), (" aapl ", "AAPL"), ("brk.b", "BRK.B"), ("BF-B", "BF-B"), ("V", "V")],
)
def test_normalize_ticker_valid(raw, expected):
    assert normalize_ticker(raw) == expected


@pytest.mark.parametrize(
    "raw", ["", "  ", "123", "AAPL;DROP", "not a ticker", "A" * 11, ".AAPL", "AA$"]
)
def test_normalize_ticker_invalid(raw):
    with pytest.raises(ValueError):
        normalize_ticker(raw)


def test_normalize_is_idempotent():
    assert normalize_ticker(normalize_ticker(" msft ")) == "MSFT"


def test_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        MarketDataSource()  # type: ignore[abstract]


def test_both_implementations_conform():
    cache = PriceCache()
    assert isinstance(SimulatorDataSource(cache), MarketDataSource)
    assert isinstance(MassiveDataSource("k", cache), MarketDataSource)
