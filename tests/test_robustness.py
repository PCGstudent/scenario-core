import numpy as np

from xtra_takehome.model import GarchTGenerator, GarchTParams
from xtra_takehome.robustness import baseline_fourth_moment_coefficient


def test_fourth_moment_coefficient_matches_formula():
    model = GarchTGenerator()
    model.params_ = GarchTParams(
        mu=0.0,
        omega=0.1,
        alpha=0.08,
        beta=0.90,
        nu=6.0,
        initial_variance=1.0,
    )
    expected_ez4 = 3.0 * (6.0 - 2.0) / (6.0 - 4.0)
    expected = 0.08**2 * expected_ez4 + 2.0 * 0.08 * 0.90 + 0.90**2
    assert np.isclose(baseline_fourth_moment_coefficient(model), expected)


def test_fourth_moment_is_infinite_when_nu_not_above_four():
    model = GarchTGenerator()
    model.params_ = GarchTParams(
        mu=0.0,
        omega=0.1,
        alpha=0.08,
        beta=0.90,
        nu=4.0,
        initial_variance=1.0,
    )
    assert np.isinf(baseline_fourth_moment_coefficient(model))
