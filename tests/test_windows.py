import numpy as np
import pytest
from scipy import stats

from xtra_takehome.windows import (
    compute_window_stats,
    non_overlapping_block_count,
    non_overlapping_blocks,
    rolling_blocks,
)


def test_rolling_blocks_shape_and_content():
    x = np.arange(10, dtype=float)
    blocks = rolling_blocks(x, horizon=4)
    assert blocks.shape == (7, 4)
    np.testing.assert_array_equal(blocks[0], [0.0, 1.0, 2.0, 3.0])
    np.testing.assert_array_equal(blocks[-1], [6.0, 7.0, 8.0, 9.0])


def test_rolling_blocks_rejects_short_series():
    with pytest.raises(ValueError):
        rolling_blocks(np.arange(3, dtype=float), horizon=4)


def test_non_overlapping_block_count():
    # 4158 daily returns contain 16 disjoint trading years, not 3907 -- and even
    # those 16 are not 16 independent draws, which the docstring is careful about.
    assert non_overlapping_block_count(4158, 252) == 16
    assert non_overlapping_block_count(251, 252) == 0


def test_non_overlapping_blocks_do_not_share_observations():
    x = np.arange(25, dtype=float)
    blocks = non_overlapping_blocks(x, horizon=10)
    assert blocks.shape == (2, 10)
    np.testing.assert_array_equal(blocks[0], np.arange(10))
    np.testing.assert_array_equal(blocks[1], np.arange(10, 20))
    assert not set(blocks[0]).intersection(blocks[1])


def test_non_overlapping_blocks_rejects_short_series():
    with pytest.raises(ValueError):
        non_overlapping_blocks(np.arange(5, dtype=float), horizon=10)


def test_overlapping_maximum_can_exceed_the_disjoint_maximum():
    """Why the extreme-region threshold uses disjoint blocks.

    A path-dependent statistic such as drawdown can be larger in a sliding window
    that straddles two calendar years than in either year alone, so the sliding
    maximum overstates the worst year actually observed.
    """
    rng = np.random.default_rng(3)
    x = rng.normal(size=600)
    x[295:305] -= 6.0  # a shock placed across a block boundary
    sliding = compute_window_stats(rolling_blocks(x, 100), acf_lags=3)
    disjoint = compute_window_stats(non_overlapping_blocks(x, 100), acf_lags=3)
    assert sliding.max_drawdown.max() >= disjoint.max_drawdown.max()


def test_window_stats_match_direct_per_block_computation():
    rng = np.random.default_rng(0)
    blocks = rng.standard_t(df=6, size=(20, 60))
    ws = compute_window_stats(blocks, acf_lags=5)

    assert ws.horizon == 60
    assert ws.n_blocks == 20
    np.testing.assert_allclose(ws.mean, blocks.mean(axis=1))
    np.testing.assert_allclose(ws.volatility, blocks.std(axis=1, ddof=1))
    np.testing.assert_allclose(ws.skewness, stats.skew(blocks, axis=1, bias=False))
    np.testing.assert_allclose(ws.q01, np.quantile(blocks, 0.01, axis=1))
    # ES is a positive loss magnitude and never below the corresponding VaR.
    assert np.all(ws.es99 >= ws.var99)
    assert np.all(ws.max_drawdown >= 0.0)


def test_window_stats_acf_is_averaged_not_concatenated():
    """Lag 0 is 1 in every block, so the average must be exactly 1."""
    rng = np.random.default_rng(1)
    ws = compute_window_stats(rng.normal(size=(15, 80)), acf_lags=4)
    assert ws.mean_squared_acf.shape == (5,)
    assert np.isclose(ws.mean_squared_acf[0], 1.0)
    assert np.all(ws.squared_acf_p05 <= ws.squared_acf_p95)


def test_short_block_acf_is_biased_toward_zero():
    """The estimator bias that motivates horizon-matched validation."""
    rng = np.random.default_rng(2)
    long_series = rng.normal(size=20_000) ** 2
    from statsmodels.tsa.stattools import acf as _acf

    long_lag1 = abs(_acf(long_series, nlags=1, fft=True)[1])
    short = compute_window_stats(
        rng.normal(size=(200, 60)), acf_lags=1
    ).mean_squared_acf[1]
    # Both are white noise, but the short-block estimate is systematically negative.
    assert short < 0.0
    assert long_lag1 < abs(short)


def test_compute_window_stats_rejects_non_2d():
    with pytest.raises(ValueError):
        compute_window_stats(np.arange(10, dtype=float))
