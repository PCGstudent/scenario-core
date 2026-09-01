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


def test_pooled_and_block_drawdown_paths_agree():
    """The two code paths that produce historical drawdowns must not diverge.

    `validate` reaches them through `historical_rolling_drawdowns`; the
    horizon-matched family reaches them through `compute_window_stats`. Both feed
    the same gate, so a discrepancy would make the two families incomparable.
    """
    from xtra_takehome.metrics import historical_rolling_drawdowns
    from xtra_takehome.windows import compute_window_stats, rolling_blocks

    returns = np.random.default_rng(5).standard_t(df=5, size=600)
    pooled = historical_rolling_drawdowns(returns, horizon=60)
    blocks = compute_window_stats(rolling_blocks(returns, 60), acf_lags=3)

    np.testing.assert_allclose(pooled, blocks.max_drawdown)


def test_var_es_match_hand_computed_values():
    """Pin the sign convention: VaR/ES are positive loss magnitudes."""
    returns = np.array([-5.0, -4.0, -3.0, -2.0, -1.0, 0.0, 1.0, 2.0, 3.0, 4.0])
    var, es = var_es(returns, 0.90)
    # Losses are [5,4,3,2,1,0,-1,-2,-3,-4]; the 90% quantile is 4.1 by linear
    # interpolation, and only the 5.0 loss lies at or above it.
    assert np.isclose(var, 4.1)
    assert np.isclose(es, 5.0)
