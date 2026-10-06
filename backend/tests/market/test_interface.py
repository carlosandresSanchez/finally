from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.interface import MarketDataSource
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


def test_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        MarketDataSource()  # type: ignore[abstract]


def test_incomplete_subclass_rejected():
    class Partial(MarketDataSource):
        async def start(self, tickers): ...

    with pytest.raises(TypeError):
        Partial()  # type: ignore[abstract]


def _sources():
    cache = PriceCache()
    with patch("app.market.massive_client.RESTClient"):
        return [SimulatorDataSource(cache), MassiveDataSource("k", cache)]


@pytest.mark.parametrize("src", _sources(), ids=["simulator", "massive"])
def test_implementations_conform(src):
    assert isinstance(src, MarketDataSource)
    assert src.get_tickers() == []
    for name in ("start", "stop", "add_ticker", "remove_ticker", "get_tickers"):
        assert callable(getattr(src, name))
