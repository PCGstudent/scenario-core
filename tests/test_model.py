import numpy as np

from xtra_takehome.model import GarchTParams, simulate_garch_t


def _params():
    return GarchTParams(
        mu=0.02,
        omega=0.05,
        alpha=0.08,
        beta=0.90,
        nu=6.0,
        initial_variance=1.0,
    )


def test_simulation_is_reproducible():
    a = simulate_garch_t(_params(), horizon=50, n_paths=4, seed=42)
    b = simulate_garch_t(_params(), horizon=50, n_paths=4, seed=42)
    np.testing.assert_array_equal(a, b)


def test_simulation_seed_changes_output():
    a = simulate_garch_t(_params(), horizon=50, n_paths=4, seed=42)
    b = simulate_garch_t(_params(), horizon=50, n_paths=4, seed=43)
    assert not np.array_equal(a, b)


def test_simulation_shape_and_finite_values():
    x = simulate_garch_t(_params(), horizon=252, n_paths=20, seed=42)
    assert x.shape == (20, 252)
    assert np.isfinite(x).all()
