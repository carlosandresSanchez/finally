from unittest.mock import patch

import pytest

from app.market.cache import PriceCache
from app.market.factory import create_market_data_source
from app.market.massive_client import MassiveDataSource
from app.market.simulator import SimulatorDataSource


def test_no_key_gives_simulator(monkeypatch):
    monkeypatch.delenv("MASSIVE_API_KEY", raising=False)
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


@pytest.mark.parametrize("value", ["", "   "])
def test_blank_key_gives_simulator(monkeypatch, value):
    monkeypatch.setenv("MASSIVE_API_KEY", value)
    assert isinstance(create_market_data_source(PriceCache()), SimulatorDataSource)


def test_key_gives_massive(monkeypatch):
    monkeypatch.setenv("MASSIVE_API_KEY", " abc ")
    with patch("app.market.massive_client.RESTClient") as rc:
        src = create_market_data_source(PriceCache())
    assert isinstance(src, MassiveDataSource)
    rc.assert_called_once_with(api_key="abc")
