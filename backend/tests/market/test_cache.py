import threading

from app.market.cache import PriceCache


def test_first_update_is_flat():
    u = PriceCache().update("AAPL", 190.0)
    assert u.previous_price == 190.0
    assert u.direction == "flat"


def test_direction_across_updates():
    c = PriceCache()
    c.update("AAPL", 190.0)
    assert c.update("AAPL", 191.0).direction == "up"
    assert c.update("AAPL", 190.5).direction == "down"
    assert c.get("AAPL").previous_price == 191.0


def test_prices_rounded_to_cents():
    assert PriceCache().update("AAPL", 190.12345).price == 190.12


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


def test_explicit_session_open_replaces_inherited():
    c = PriceCache()
    c.update("MSFT", 415.0)
    assert c.update("MSFT", 416.0, session_open=420.0).session_open == 420.0


def test_zero_timestamp_is_respected():
    assert PriceCache().update("AAPL", 1.0, timestamp=0.0).timestamp == 0.0


def test_default_timestamp():
    assert PriceCache().update("AAPL", 1.0).timestamp > 0


def test_get_missing():
    c = PriceCache()
    assert c.get("NOPE") is None
    assert c.get_price("NOPE") is None


def test_get_price():
    c = PriceCache()
    c.update("AAPL", 190.0)
    assert c.get_price("AAPL") == 190.0


def test_get_all_is_a_copy():
    c = PriceCache()
    c.update("AAPL", 190.0)
    snap = c.get_all()
    snap.clear()
    assert "AAPL" in c


def test_version_increments_on_update():
    c = PriceCache()
    assert c.version == 0
    c.update("AAPL", 1.0)
    c.update("AAPL", 1.0)
    assert c.version == 2


def test_remove_bumps_version():
    c = PriceCache()
    c.update("AAPL", 1.0)
    v = c.version
    c.remove("AAPL")
    assert c.version == v + 1
    assert c.get("AAPL") is None
    c.remove("AAPL")
    assert c.version == v + 1  # no-op doesn't bump


def test_snapshot_is_atomic_pair():
    c = PriceCache()
    c.update("AAPL", 1.0)
    version, prices = c.snapshot()
    assert version == c.version
    assert set(prices) == {"AAPL"}


def test_len_and_contains():
    c = PriceCache()
    assert len(c) == 0
    c.update("AAPL", 1.0)
    assert len(c) == 1
    assert "AAPL" in c
    assert "MSFT" not in c


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
    assert c.version == 8000
    assert len(c) == 8
