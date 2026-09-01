import numpy as np

from xtra_takehome.challenger import GjrSkewTGenerator, GjrSkewTParams


def _fitted_like_generator() -> GjrSkewTGenerator:
    g = GjrSkewTGenerator()
    g.params_ = GjrSkewTParams(
        mu=0.02,
        omega=0.05,
        alpha=0.06,
        gamma=0.08,
        beta=0.88,
        eta=6.0,
        lam=-0.15,
    )
    g._residuals = np.array([-1.5, -0.3, 0.2, 0.8, 1.2], dtype=float)
    g._variances = np.array([1.8, 0.9, 0.7, 1.0, 1.4], dtype=float)
    return g


def test_challenger_simulation_is_reproducible():
    g = _fitted_like_generator()
    a = g.simulate(n_steps=50, n_paths=8, seed=42)
    b = g.simulate(n_steps=50, n_paths=8, seed=42)
    np.testing.assert_array_equal(a, b)


def test_challenger_shape_and_finite_values():
    g = _fitted_like_generator()
    x = g.simulate(n_steps=252, n_paths=20, seed=42)
    assert x.shape == (20, 252)
    assert np.isfinite(x).all()


def test_symmetric_skewt_recovers_half_negative_second_moment():
    p = GjrSkewTParams(
        mu=0.0,
        omega=0.05,
        alpha=0.06,
        gamma=0.08,
        beta=0.88,
        eta=6.0,
        lam=0.0,
    )
    m2_negative, _, _ = p.innovation_moments()
    assert np.isclose(m2_negative, 0.5, atol=1e-10)
    assert np.isclose(
        p.effective_persistence,
        p.alpha + p.beta + 0.5 * p.gamma,
        atol=1e-10,
    )


def test_fourth_moment_coefficient_is_positive_and_finite_when_eta_gt_four():
    p = _fitted_like_generator().params_
    assert p is not None
    assert np.isfinite(p.fourth_moment_coefficient)
    assert p.fourth_moment_coefficient > 0.0


def test_consecutive_seeds_do_not_share_a_generator_stream():
    """Replications must be independent.

    Deriving the innovation stream as `default_rng(seed + 1)` would make the
    innovations of seed s the state stream of seed s+1, so the seeds in the
    robustness study would not be independent replications.
    """
    g = _fitted_like_generator()
    a = g.simulate(n_steps=40, n_paths=6, seed=40)
    b = g.simulate(n_steps=40, n_paths=6, seed=41)

    assert not np.array_equal(a, b)
    # Independent streams: no path of one run is reproduced by the other.
    for row in a:
        assert not any(np.array_equal(row, other) for other in b)


def test_implied_unconditional_variance_matches_the_persistence_identity():
    p = _fitted_like_generator().params_
    assert p is not None
    expected = p.omega / (1.0 - p.effective_persistence)
    assert np.isclose(p.implied_unconditional_variance, expected)


def test_implied_unconditional_variance_is_infinite_at_unit_persistence():
    p = GjrSkewTParams(
        mu=0.0, omega=0.05, alpha=0.10, gamma=0.0, beta=0.90, eta=6.0, lam=0.0
    )
    assert np.isinf(p.implied_unconditional_variance)
