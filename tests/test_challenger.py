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
