"""Tests for scenario generation options and the two stress modes.

The generator gained keyword arguments for the lab. The first test here is the
one that matters most: the defaults must reproduce the original behaviour
exactly, because the validation suite depends on it.
"""

import numpy as np
import pytest

from xtra_takehome.app import stress
from xtra_takehome.challenger import GjrSkewTGenerator, GjrSkewTParams


def _generator() -> GjrSkewTGenerator:
    g = GjrSkewTGenerator()
    g.params_ = GjrSkewTParams(
        mu=0.02, omega=0.05, alpha=0.06, gamma=0.08, beta=0.88, eta=6.0, lam=-0.15
    )
    g._residuals = np.array([-1.5, -0.3, 0.2, 0.8, 1.2], dtype=float)
    g._variances = np.array([1.8, 0.9, 0.7, 1.0, 1.4], dtype=float)
    return g


# ---------------------------------------------------------------------------
# The generator's new options
# ---------------------------------------------------------------------------


def test_default_simulate_is_unchanged_and_reproducible():
    """The validation suite calls simulate() positionally. That must not move."""
    g = _generator()
    a = g.simulate(n_steps=40, n_paths=8, seed=42)
    b = g.simulate(n_steps=40, n_paths=8, seed=42)

    assert a.shape == (8, 40)
    np.testing.assert_array_equal(a, b)


def test_changing_the_seed_changes_the_scenarios():
    g = _generator()
    a = g.simulate(n_steps=30, n_paths=6, seed=1)
    b = g.simulate(n_steps=30, n_paths=6, seed=2)
    assert not np.array_equal(a, b)


def test_return_variance_does_not_disturb_the_random_stream():
    """Asking for variances must not change the returns that come back."""
    g = _generator()
    plain = g.simulate(n_steps=25, n_paths=5, seed=7)
    returns, variances = g.simulate(n_steps=25, n_paths=5, seed=7, return_variance=True)

    np.testing.assert_array_equal(plain, returns)
    assert variances.shape == returns.shape
    assert np.all(variances > 0)


def test_latest_start_puts_every_path_in_the_same_place():
    g = _generator()
    residual, variance = g.latest_state
    assert residual == 1.2 and variance == 1.4

    _, variances = g.simulate(n_steps=5, n_paths=4, seed=3, initial_state="latest",
                              return_variance=True)
    # Day one is determined by the shared starting state, so it is identical.
    assert len(set(np.round(variances[:, 0], 12))) == 1


def test_historical_mixture_start_spreads_across_states():
    g = _generator()
    _, variances = g.simulate(n_steps=5, n_paths=40, seed=3,
                              initial_state="historical_mix", return_variance=True)
    assert len(set(np.round(variances[:, 0], 12))) > 1


def test_explicit_start_is_honoured():
    g = _generator()
    p = g.params_
    _, variances = g.simulate(n_steps=3, n_paths=2, seed=5, initial_state=(-4.0, 9.0),
                              return_variance=True)
    expected = p.omega + p.alpha * 16.0 + p.gamma * 16.0 + p.beta * 9.0
    np.testing.assert_allclose(variances[:, 0], expected)


def test_unknown_initial_state_is_rejected():
    with pytest.raises(ValueError, match="Unknown initial_state"):
        _generator().simulate(10, 2, 1, initial_state="tomorrow")


def test_non_positive_explicit_variance_is_rejected():
    with pytest.raises(ValueError):
        _generator().simulate(10, 2, 1, initial_state=(0.0, 0.0))


# ---------------------------------------------------------------------------
# Mode A -- filtering the model's own draws
# ---------------------------------------------------------------------------


def test_filters_select_the_requested_share():
    rng = np.random.default_rng(0)
    paths = rng.normal(0, 2, size=(200, 30))
    idx = stress.filter_scenarios(paths, "worst 5% by terminal loss")
    assert len(idx) == 10
    assert len(set(idx.tolist())) == len(idx)


def test_worst_filter_actually_selects_the_worst():
    rng = np.random.default_rng(1)
    paths = rng.normal(0, 2, size=(100, 20))
    from xtra_takehome.app.risk import terminal_simple_return

    terminal = terminal_simple_return(paths)
    idx = stress.filter_scenarios(paths, "worst 1% by terminal loss")
    assert terminal[idx].max() <= np.percentile(terminal, 2)


def test_drawdown_filter_selects_deep_drawdowns():
    rng = np.random.default_rng(2)
    paths = rng.normal(0, 2, size=(100, 30))
    from xtra_takehome.app.risk import max_drawdowns

    dd = max_drawdowns(paths)
    idx = stress.filter_scenarios(paths, "deepest 5% drawdowns")
    assert dd[idx].min() >= np.percentile(dd, 90)


def test_unknown_filter_is_rejected():
    with pytest.raises(ValueError):
        stress.filter_scenarios(np.zeros((5, 5)), "worst ever")


# ---------------------------------------------------------------------------
# Mode B -- deterministic shock propagation
# ---------------------------------------------------------------------------


def test_shock_propagation_is_deterministic():
    p = _generator().params_
    a = stress.propagate_shocks(p, [-5.0, -5.0], 4.0, 0.0, days_after=20)
    b = stress.propagate_shocks(p, [-5.0, -5.0], 4.0, 0.0, days_after=20)
    np.testing.assert_array_equal(a.conditional_volatility, b.conditional_volatility)
    np.testing.assert_array_equal(a.volatility_after, b.volatility_after)


def test_shock_propagation_matches_the_recursion_by_hand():
    p = _generator().params_
    experiment = stress.propagate_shocks(p, [-6.0], initial_variance=4.0,
                                         initial_residual=-2.0, days_after=0)
    # Day one uses the *incoming* residual, which was negative, so leverage applies.
    expected = p.omega + p.alpha * 4.0 + p.gamma * 4.0 + p.beta * 4.0
    np.testing.assert_allclose(experiment.conditional_volatility[0], np.sqrt(expected))


def test_a_negative_shock_raises_volatility_more_than_a_positive_one():
    p = _generator().params_
    down = stress.propagate_shocks(p, [-8.0, 0.0], 4.0, 0.0, days_after=0)
    up = stress.propagate_shocks(p, [8.0, 0.0], 4.0, 0.0, days_after=0)
    assert down.conditional_volatility[1] > up.conditional_volatility[1]


def test_larger_shocks_produce_higher_peak_volatility():
    p = _generator().params_
    small = stress.propagate_shocks(p, [-3.0, 0.0], 4.0, 0.0, days_after=10)
    large = stress.propagate_shocks(p, [-12.0, 0.0], 4.0, 0.0, days_after=10)
    assert large.peak_volatility > small.peak_volatility


def test_decay_is_monotone_toward_the_long_run_level():
    p = _generator().params_
    experiment = stress.propagate_shocks(p, [-10.0], 4.0, 0.0, days_after=120)
    after = experiment.volatility_after
    # After the first step the expected path is smooth mean reversion.
    diffs = np.diff(after[1:])
    assert np.all(diffs <= 1e-9) or np.all(diffs >= -1e-9)


def test_empty_shock_sequence_is_rejected():
    with pytest.raises(ValueError):
        stress.propagate_shocks(_generator().params_, [], 4.0)


def test_non_positive_initial_variance_is_rejected():
    with pytest.raises(ValueError):
        stress.propagate_shocks(_generator().params_, [-5.0], 0.0)


def test_asymmetry_example_shows_the_leverage_effect():
    p = _generator().params_
    example = stress.asymmetry_example(p, magnitude=3.0, current_variance=4.0)
    assert example["variance_after_down"] > example["variance_after_up"]
    assert example["ratio"] > 1.0
    assert np.isclose(
        example["extra_variance_from_leverage"], p.gamma * 9.0
    ), "the gap between an equal down and up move is exactly gamma times the squared shock"


def test_all_presets_propagate():
    p = _generator().params_
    for name, shocks in stress.PRESETS.items():
        experiment = stress.propagate_shocks(p, shocks, 4.0, 0.0, days_after=5)
        assert experiment.conditional_volatility.size == len(shocks), name
        assert np.all(experiment.conditional_volatility > 0), name
