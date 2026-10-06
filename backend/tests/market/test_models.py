import dataclasses

import pytest

from app.market.models import PriceUpdate


def make(price=191.20, prev=191.15, open_=190.0):
    return PriceUpdate("AAPL", price=price, previous_price=prev, timestamp=1707580800.5,
                       session_open=open_)


def test_direction_up_down_flat():
    assert make(10, 9).direction == "up"
    assert make(9, 10).direction == "down"
    assert make(10, 10).direction == "flat"


def test_tick_change_and_percent():
    u = make(101, 100)
    assert u.change == 1.0
    assert u.change_percent == 1.0


def test_change_percent_zero_previous():
    assert make(1, 0).change_percent == 0.0


def test_day_change():
    u = make()
    assert u.day_change == 1.2
    assert u.day_change_percent == 0.6316


def test_day_change_without_session_open():
    u = make(open_=None)
    assert u.day_change == 0.0
    assert u.day_change_percent == 0.0


def test_default_timestamp_is_set():
    assert PriceUpdate("A", 1.0, 1.0).timestamp > 0


def test_frozen():
    with pytest.raises(dataclasses.FrozenInstanceError):
        make().price = 1.0  # type: ignore[misc]


def test_to_dict_keys_and_values():
    d = make().to_dict()
    assert set(d) == {
        "ticker", "price", "previous_price", "timestamp", "change", "change_percent",
        "direction", "session_open", "day_change", "day_change_percent",
    }
    assert d["direction"] == "up"
    assert d["day_change"] == 1.2
    assert d["timestamp"] == 1707580800.5
