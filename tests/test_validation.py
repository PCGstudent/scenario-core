import numpy as np
import pandas as pd
import pytest

from xtra_takehome.validation import (
    THRESHOLDS,
    exceedance_checks,
    explosive_path_sensitivity,
    poisson_count_interval,
    validate,
    validate_horizon_matched,
)
from xtra_takehome.windows import compute_window_stats, rolling_blocks

HORIZON = 60
ACF_LAGS = 5


def _returns(seed: int = 0, n: int = 1200) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.standard_t(df=5, size=n))


def _paths(seed: int = 1, n_paths: int = 40) -> np.ndarray:
    rng = np.random.default_rng(seed)
    return rng.standard_t(df=5, size=(n_paths, HORIZON))


def test_both_families_use_the_same_declared_thresholds():
    """The central honesty invariant: changing the estimator never changes a gate."""
    real = _returns()
    paths = _paths()
    pooled, _ = validate(real, paths, horizon=HORIZON, acf_lags=ACF_LAGS)
    matched = validate_horizon_matched(
        compute_window_stats(rolling_blocks(real.to_numpy(), HORIZON), ACF_LAGS),
        compute_window_stats(paths, ACF_LAGS),
    )

    assert [g.metric for g in pooled] == [g.metric for g in matched]
    for a, b in zip(pooled, matched):
        assert (a.threshold, a.error_type) == (b.threshold, b.error_type)
        assert (a.error_type, a.threshold) == THRESHOLDS[a.metric]


def test_every_declared_threshold_is_exercised():
    pooled, _ = validate(_returns(), _paths(), horizon=HORIZON, acf_lags=ACF_LAGS)
    assert {g.metric for g in pooled} == set(THRESHOLDS)


def test_gate_pass_flag_follows_error_and_threshold():
    pooled, _ = validate(_returns(), _paths(), horizon=HORIZON, acf_lags=ACF_LAGS)
    for g in pooled:
        assert g.passed == (g.error <= g.threshold)


def test_horizon_matched_requires_equal_block_lengths():
    real = compute_window_stats(np.zeros((5, 30)) + np.arange(30), ACF_LAGS)
    synthetic = compute_window_stats(np.zeros((5, 20)) + np.arange(20), ACF_LAGS)
    with pytest.raises(ValueError):
        validate_horizon_matched(real, synthetic)


def test_horizon_matched_bands_are_ordered():
    real = _returns()
    matched = validate_horizon_matched(
        compute_window_stats(rolling_blocks(real.to_numpy(), HORIZON), ACF_LAGS),
        compute_window_stats(_paths(), ACF_LAGS),
    )
    banded = [g for g in matched if g.real_band is not None]
    assert banded, "median gates should carry dispersion bands"
    for g in banded:
        assert g.real_band[0] <= g.real_band[1]
        assert g.synthetic_band[0] <= g.synthetic_band[1]


def test_poisson_interval_for_one_event_matches_known_values():
    lower, upper = poisson_count_interval(observed=1, coverage=0.90)
    # Exact Garwood interval: [chi2_{0.05,2}/2, chi2_{0.95,4}/2].
    assert np.isclose(lower, 0.05129, atol=1e-4)
    assert np.isclose(upper, 4.74386, atol=1e-4)
    assert poisson_count_interval(observed=0)[0] == 0.0


def test_exceedance_check_flags_a_model_that_never_reaches_the_worst_year():
    real = compute_window_stats(
        rolling_blocks(_returns(seed=3).to_numpy(), HORIZON), ACF_LAGS
    )
    # A generator with a quarter of the historical scale can never exceed anything.
    tame = compute_window_stats(_paths(seed=4) * 0.25, ACF_LAGS)
    checks = exceedance_checks(real, tame, n_observations=1200)

    assert {c.statistic for c in checks} == {
        "volatility",
        "VaR 99%",
        "ES 99%",
        "maximum drawdown",
    }
    for c in checks:
        assert c.independent_years == 1200 // HORIZON
        assert c.synthetic_exceedance_probability == 0.0
        assert not c.passed  # zero exceedance probability is below the interval


def test_exceedance_check_passes_for_a_comparable_generator():
    real = compute_window_stats(
        rolling_blocks(_returns(seed=5).to_numpy(), HORIZON), ACF_LAGS
    )
    similar = compute_window_stats(_paths(seed=6, n_paths=400), ACF_LAGS)
    checks = exceedance_checks(real, similar, n_observations=1200)
    assert any(c.passed for c in checks)


def test_explosive_path_sensitivity_removes_the_most_volatile_paths():
    rng = np.random.default_rng(7)
    paths = rng.normal(size=(50, HORIZON))
    paths[0] *= 50.0  # one runaway path
    stats_ = compute_window_stats(paths, ACF_LAGS)

    rows = explosive_path_sensitivity(paths, stats_.volatility, drops=(0, 1))
    assert [r.dropped for r in rows] == [0, 1]
    assert rows[0].fraction == 0.0
    assert np.isclose(rows[1].fraction, 1 / 50)
    # Dropping the single runaway path must collapse the pooled moments.
    assert rows[1].volatility < rows[0].volatility / 2
    assert abs(rows[1].excess_kurtosis) < abs(rows[0].excess_kurtosis)


def test_explosive_path_sensitivity_skips_drops_larger_than_the_simulation():
    paths = np.random.default_rng(8).normal(size=(3, HORIZON))
    stats_ = compute_window_stats(paths, ACF_LAGS)
    rows = explosive_path_sensitivity(paths, stats_.volatility, drops=(0, 10))
    assert [r.dropped for r in rows] == [0]
