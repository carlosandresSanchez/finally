import threading

from app.market.cache import PriceCache
from app.market.models import PriceUpdate


def test_price_update_derived_values():
    u = PriceUpdate("AAPL", 191.20, 191.15, timestamp=1707580800.5, session_open=190.00)
    assert u.direction == "up"
    assert u.change == 0.05
    assert u.day_change == 1.2
    assert u.day_change_percent == 0.6316


def test_directions():
    assert PriceUpdate("A", 1.0, 2.0).direction == "down"
    assert PriceUpdate("A", 2.0, 2.0).direction == "flat"


def test_zero_reference_prices_do_not_divide():
    u = PriceUpdate("A", 5.0, 0.0, session_open=None)
    assert u.change_percent == 0.0
    assert u.day_change == 0.0
    assert u.day_change_percent == 0.0


def test_to_dict_has_wire_keys():
    d = PriceUpdate("A", 2.0, 1.0, timestamp=1.0, session_open=1.0).to_dict()
    assert set(d) == {
        "ticker", "price", "previous_price", "timestamp", "change", "change_percent",
        "direction", "session_open", "day_change", "day_change_percent",
    }
    assert d["direction"] == "up" and d["day_change_percent"] == 100.0


def test_first_update_is_flat():
    u = PriceCache().update("AAPL", 190.0)
    assert u.direction == "flat" and u.previous_price == 190.0


def test_day_change_uses_session_open():
    c = PriceCache()
    c.update("AAPL", 190.00)
    u = c.update("AAPL", 191.90)
    assert u.session_open == 190.00
    assert u.day_change_percent == 1.0
    assert u.to_dict()["day_change"] == 1.9


def test_explicit_session_open_wins():
    u = PriceCache().update("MSFT", 415.0, session_open=420.0)
    assert u.day_change == -5.0


def test_zero_timestamp_is_respected():
    assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0


def test_prices_are_rounded():
    assert PriceCache().update("AAPL", 190.12345).price == 190.12


def test_get_helpers():
    c = PriceCache()
    assert c.get("X") is None and c.get_price("X") is None and "X" not in c
    c.update("X", 3.0)
    assert c.get_price("X") == 3.0 and "X" in c and len(c) == 1
    assert set(c.get_all()) == {"X"}


def test_remove_bumps_version():
    c = PriceCache()
    c.update("AAPL", 1.0)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    c.remove("AAPL")
    assert c.version == v + 1  # no-op doesn't bump


def test_snapshot_is_atomic_pair():
    c = PriceCache()
    c.update("AAPL", 1.0)
    version, prices = c.snapshot()
    assert version == c.version and set(prices) == {"AAPL"}


def test_get_all_is_a_copy():
    c = PriceCache()
    c.update("AAPL", 1.0)
    c.get_all().clear()
    assert "AAPL" in c


def test_concurrent_writers():
    c = PriceCache()

    def writer(t):
        for i in range(1000):
            c.update(t, 100 + i)

    threads = [threading.Thread(target=writer, args=(f"T{i}",)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert c.version == 8000 and len(c) == 8
