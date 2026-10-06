import numpy as np
import pytest

from app.market.seed_prices import SEED_PRICES, TICKER_PARAMS
from app.market.simulator import GBMSimulator


def test_step_returns_all_tickers_positive():
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], seed=1)
    prices = sim.step()
    assert set(prices) == {"AAPL", "MSFT", "JPM"}
    assert all(p > 0 for p in prices.values())


def test_empty_simulator_steps():
    assert GBMSimulator([]).step() == {}


def test_seed_prices_used():
    sim = GBMSimulator(list(SEED_PRICES))
    for t, p in SEED_PRICES.items():
        assert sim.get_price(t) == p
        assert sim.get_session_open(t) == p


def test_unknown_ticker_gets_random_price_in_range():
    sim = GBMSimulator(["PYPL"], seed=5)
    assert 50 <= sim.get_price("PYPL") <= 300


def test_prices_never_negative_over_many_steps():
    sim = GBMSimulator(["TSLA", "NVDA"], seed=2)
    for _ in range(2000):
        assert all(p > 0 for p in sim.step().values())


def test_seeded_simulator_is_deterministic():
    a = GBMSimulator(["AAPL", "TSLA"], seed=7)
    b = GBMSimulator(["AAPL", "TSLA"], seed=7)
    assert [a.step() for _ in range(100)] == [b.step() for _ in range(100)]


def test_different_seeds_differ():
    a = GBMSimulator(["AAPL"], seed=1)
    b = GBMSimulator(["AAPL"], seed=2)
    assert [a.step() for _ in range(20)] != [b.step() for _ in range(20)]


def test_cholesky_full_default_set_and_many_unknowns():
    GBMSimulator(list(SEED_PRICES))
    sim = GBMSimulator(list(SEED_PRICES) + [f"ZZ{i}" for i in range(40)])
    assert sim._cholesky is not None
    assert sim._cholesky.shape == (50, 50)


def test_cholesky_none_for_single_ticker():
    assert GBMSimulator(["AAPL"])._cholesky is None


def test_ticker_normalization():
    sim = GBMSimulator(["aapl"])
    sim.add_ticker(" AAPL ")
    assert sim.get_tickers() == ["AAPL"]


def test_invalid_ticker_rejected():
    with pytest.raises(ValueError):
        GBMSimulator(["bad ticker"])
    with pytest.raises(ValueError):
        GBMSimulator([]).add_ticker("123")


def test_add_and_remove_ticker():
    sim = GBMSimulator(["AAPL"], seed=1)
    sim.add_ticker("MSFT")
    assert sim.get_tickers() == ["AAPL", "MSFT"]
    assert set(sim.step()) == {"AAPL", "MSFT"}
    sim.remove_ticker("AAPL")
    assert sim.get_tickers() == ["MSFT"]
    assert sim.get_price("AAPL") is None
    assert sim.get_session_open("AAPL") is None
    sim.remove_ticker("AAPL")  # no-op
    assert set(sim.step()) == {"MSFT"}


def test_add_existing_ticker_is_noop():
    sim = GBMSimulator(["AAPL"])
    before = sim.get_price("AAPL")
    sim.add_ticker("AAPL")
    assert sim.get_tickers() == ["AAPL"]
    assert sim.get_price("AAPL") == before


def test_session_open_stays_fixed_while_price_moves():
    sim = GBMSimulator(["AAPL"], seed=1)
    for _ in range(50):
        sim.step()
    assert sim.get_session_open("AAPL") == 190.0


def test_tick_volatility_is_realistic():
    sim = GBMSimulator(["AAPL"], event_probability=0.0, seed=1)
    prices = [sim.step()["AAPL"] for _ in range(5000)]
    rets = np.diff(np.log(prices))
    assert 0.00003 < rets.std() < 0.0002  # ~ 0.22 * sqrt(dt) = 6.4e-5 (+ cent rounding)


def test_events_fire():
    sim = GBMSimulator(["AAPL"], event_probability=1.0, seed=3)
    p0 = sim.get_price("AAPL")
    p1 = sim.step()["AAPL"]
    assert 0.019 < abs(p1 / p0 - 1) < 0.051


def test_no_events_when_probability_zero():
    sim = GBMSimulator(["AAPL"], event_probability=0.0, seed=3)
    p = sim.get_price("AAPL")
    for _ in range(500):
        new = sim.step()["AAPL"]
        assert abs(new / p - 1) < 0.01
        p = new


def test_correlated_moves_within_tech_exceed_cross_sector():
    # Compare empirical correlation of log-returns using a large dt for signal.
    sim = GBMSimulator(["AAPL", "MSFT", "JPM"], dt=1e-4, event_probability=0.0, seed=11)
    # use un-rounded internal prices to avoid cent-rounding noise
    series = {t: [] for t in sim.get_tickers()}
    for _ in range(4000):
        sim.step()
        for t in series:
            series[t].append(sim.get_price(t))
    r = {t: np.diff(np.log(v)) for t, v in series.items()}
    tech = np.corrcoef(r["AAPL"], r["MSFT"])[0, 1]
    cross = np.corrcoef(r["AAPL"], r["JPM"])[0, 1]
    assert tech > cross
    assert tech == pytest.approx(0.6, abs=0.1)
    assert cross == pytest.approx(0.3, abs=0.1)


@pytest.mark.parametrize(
    "a,b,rho",
    [
        ("AAPL", "MSFT", 0.6),
        ("JPM", "V", 0.5),
        ("TSLA", "AAPL", 0.3),
        ("AAPL", "TSLA", 0.3),
        ("AAPL", "JPM", 0.3),
        ("ZZZ", "YYY", 0.3),
    ],
)
def test_pairwise_correlation(a, b, rho):
    assert GBMSimulator._pairwise_correlation(a, b) == rho


def test_params_are_copied_per_ticker():
    sim = GBMSimulator(["AAPL"])
    sim._params["AAPL"]["sigma"] = 99
    assert TICKER_PARAMS["AAPL"]["sigma"] == 0.22
