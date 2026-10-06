import threading

from app.market.cache import PriceCache
from app.market.models import PriceUpdate


def test_first_update_is_flat():
    cache = PriceCache()
    u = cache.update("AAPL", 190.0, timestamp=1.0)
    assert u.previous_price == 190.0
    assert u.direction == "flat"
    assert u.change == 0
    assert u.timestamp == 1.0


def test_up_and_down_direction():
    cache = PriceCache()
    cache.update("AAPL", 100.0)
    up = cache.update("AAPL", 101.5)
    assert (up.direction, up.previous_price, up.change) == ("up", 100.0, 1.5)
    down = cache.update("AAPL", 100.0)
    assert (down.direction, down.change) == ("down", -1.5)


def test_get_get_all_remove():
    cache = PriceCache()
    assert cache.get("X") is None
    assert cache.get_price("X") is None
    cache.update("A", 1.0)
    cache.update("B", 2.0)
    assert cache.get_price("B") == 2.0
    assert set(cache.get_all()) == {"A", "B"}
    assert len(cache) == 2 and "A" in cache
    cache.remove("A")
    assert "A" not in cache and len(cache) == 1
    cache.remove("missing")  # no error


def test_get_all_returns_copy():
    cache = PriceCache()
    cache.update("A", 1.0)
    snapshot = cache.get_all()
    snapshot.clear()
    assert "A" in cache


def test_version_increments_on_change_only():
    cache = PriceCache()
    v0 = cache.version
    cache.update("A", 1.0)
    assert cache.version == v0 + 1
    cache.remove("missing")
    assert cache.version == v0 + 1
    cache.remove("A")
    assert cache.version == v0 + 2


def test_timestamp_defaults_to_now():
    assert PriceCache().update("A", 1.0).timestamp > 0


def test_price_update_is_frozen_and_serializable():
    u = PriceUpdate("A", 2.0, 1.0, 5.0, 1.0, "up")
    assert u.to_dict()["direction"] == "up"
    try:
        u.price = 3.0  # type: ignore[misc]
    except Exception as exc:
        assert type(exc).__name__ == "FrozenInstanceError"
    else:
        raise AssertionError("PriceUpdate should be immutable")


def test_thread_safety():
    cache = PriceCache()

    def worker(n):
        for i in range(200):
            cache.update(f"T{n}", float(i))

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(5)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(cache) == 5
    assert cache.version == 1000
