import numpy as np

from xtra_takehome.metrics import (
    max_drawdown_from_returns,
    mean_squared_return_acf,
    var_es,
)


def test_expected_shortfall_is_at_least_var():
    returns = np.array([-1.0, -2.0, -3.0, 0.5, 0.2, -7.0, 1.0])
    var, es = var_es(returns, 0.95)
    assert var >= 0.0
    assert es >= var


def test_drawdown_non_negative():
    returns = np.array([1.0, -2.0, -3.0, 1.0])
    dd = max_drawdown_from_returns(returns)
    assert 0.0 <= dd < 1.0


def test_path_acf_shape():
    rng = np.random.default_rng(1)
    paths = rng.normal(size=(10, 100))
    result = mean_squared_return_acf(paths, nlags=5)
    assert result.shape == (6,)
    assert np.isclose(result[0], 1.0)
