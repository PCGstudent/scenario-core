import numpy as np
import pandas as pd
import pytest

from xtra_takehome.diagnostics import (
    hill_estimator,
    hill_profile,
    mean_excess,
    summarize,
)


def test_hill_estimator_recovers_a_known_pareto_tail_index():
    # For X ~ Pareto(alpha) the Hill estimator is consistent for alpha.
    alpha = 3.0
    rng = np.random.default_rng(0)
    x = (1.0 - rng.random(200_000)) ** (-1.0 / alpha)
    assert np.isclose(hill_estimator(x, k=5_000), alpha, rtol=0.05)


def test_hill_estimator_orders_tails_correctly():
    """A heavier tail must produce a smaller index."""
    rng = np.random.default_rng(1)
    u = 1.0 - rng.random(100_000)
    heavy = hill_estimator(u ** (-1.0 / 2.0), k=2_000)
    light = hill_estimator(u ** (-1.0 / 5.0), k=2_000)
    assert heavy < light


def test_hill_estimator_rejects_out_of_range_k():
    x = np.array([3.0, 2.0, 1.0])
    with pytest.raises(ValueError):
        hill_estimator(x, k=0)
    with pytest.raises(ValueError):
        hill_estimator(x, k=3)


def test_hill_profile_returns_both_tails():
    rng = np.random.default_rng(2)
    returns = pd.Series(rng.standard_t(df=4, size=5_000))
    ks, left, right = hill_profile(returns, k_values=(50, 100))
    assert list(ks) == [50, 100]
    assert left.shape == right.shape == (2,)
    assert np.all(np.isfinite(left)) and np.all(np.isfinite(right))


def test_mean_excess_increases_for_a_heavy_tail():
    """A rising mean-excess function is the heavy-tail signature the report cites."""
    rng = np.random.default_rng(3)
    losses = (1.0 - rng.random(50_000)) ** (-1.0 / 2.5)
    thresholds, excess = mean_excess(pd.Series(-losses))
    assert thresholds.shape == excess.shape
    assert excess[-1] > excess[0]


def test_summarize_carries_tail_index_fields():
    rng = np.random.default_rng(4)
    summary = summarize(pd.Series(rng.standard_t(df=4, size=3_000)), nlags=5)
    assert summary.hill_k > 0
    assert np.isfinite(summary.hill_left) and summary.hill_left > 0
    assert np.isfinite(summary.hill_right) and summary.hill_right > 0
