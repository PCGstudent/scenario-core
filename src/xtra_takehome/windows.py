"""Year-level (horizon-matched) statistics.

Every statistic used to compare synthetic scenarios with history is estimated on
blocks of exactly `horizon` observations on *both* sides. This is the same
like-for-like principle already applied to drawdowns, extended to the remaining
metrics.

The motivation is not cosmetic. Several of the validation statistics are strongly
sample-size dependent:

* the sample ACF of squared returns is biased toward zero in short blocks, so a
  full-sample historical ACF cannot be compared with an ACF averaged over
  252-day synthetic paths;
* under the fitted volatility recursion the unconditional fourth moment does not
  exist, so sample kurtosis does not converge to a finite population value and
  becomes progressively more dominated by rare extremes as the sample grows; a
  pooled synthetic sample of 252,000 observations is therefore not comparable
  with 4,158 historical ones.

Comparing those statistics across different sample sizes measures the estimator,
not the model.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view
from scipy import stats
from statsmodels.tsa.stattools import acf

from .metrics import path_max_drawdowns, var_es


def rolling_blocks(returns: np.ndarray, horizon: int) -> np.ndarray:
    """Overlapping historical blocks of length `horizon`, shape (n-horizon+1, horizon).

    The blocks overlap, so they are a descriptive calibration target rather than an
    iid sample. `non_overlapping_block_count` reports how many disjoint blocks the
    sample actually contains, which is the honest upper bound on independent draws.
    """
    x = np.asarray(returns, dtype=float).reshape(-1)
    if x.size < horizon:
        raise ValueError("Not enough observations for the requested horizon.")
    return sliding_window_view(x, horizon)


def non_overlapping_block_count(n_observations: int, horizon: int) -> int:
    """How many non-overlapping blocks the sample contains.

    This is an upper bound on the number of independent observations, not the
    effective sample size: consecutive years share macro regimes and volatility
    persistence, so 16 non-overlapping years are not 16 independent draws.
    Everything said about the stressed region is limited by this count.
    """
    return int(n_observations // horizon)


def non_overlapping_blocks(returns: np.ndarray, horizon: int) -> np.ndarray:
    """Disjoint blocks of length `horizon`, taken from the start of the sample.

    Used where the analysis needs blocks that do not share observations, such as
    the maximum of the historical record: the maximum over sliding windows is
    inflated relative to the maximum over disjoint years for path-dependent
    statistics such as drawdown.
    """
    x = np.asarray(returns, dtype=float).reshape(-1)
    count = non_overlapping_block_count(x.size, horizon)
    if count < 1:
        raise ValueError("Not enough observations for one non-overlapping block.")
    return x[: count * horizon].reshape(count, horizon)


@dataclass(frozen=True)
class StressEpisode:
    """Where the most severe historical blocks actually come from."""

    statistic: str
    quantile: float
    n_blocks: int
    first_start: str
    last_start: str
    distinct_years: tuple[int, ...]


def stress_episode_span(
    values: np.ndarray,
    start_dates,
    statistic: str,
    quantile: float = 0.95,
) -> StressEpisode:
    """Identify the calendar span of the blocks above `quantile` of a statistic.

    If overlapping windows have replicated a single crisis, those blocks are
    contiguous in time and the span is short. That is what makes an upper
    historical quantile unusable as a gate target.
    """
    x = np.asarray(values, dtype=float)
    threshold = float(np.quantile(x, quantile))
    selected = np.flatnonzero(x >= threshold)
    dates = start_dates[selected]
    return StressEpisode(
        statistic=statistic,
        quantile=quantile,
        n_blocks=int(selected.size),
        first_start=str(min(dates).date()),
        last_start=str(max(dates).date()),
        distinct_years=tuple(sorted({int(d.year) for d in dates})),
    )


@dataclass(frozen=True)
class WindowStats:
    """Per-block statistics for a set of equal-length return blocks."""

    horizon: int
    n_blocks: int
    mean: np.ndarray
    volatility: np.ndarray
    skewness: np.ndarray
    excess_kurtosis: np.ndarray
    q01: np.ndarray
    q05: np.ndarray
    q95: np.ndarray
    q99: np.ndarray
    var95: np.ndarray
    es95: np.ndarray
    var99: np.ndarray
    es99: np.ndarray
    max_drawdown: np.ndarray
    mean_squared_acf: np.ndarray
    squared_acf_p05: np.ndarray
    squared_acf_p95: np.ndarray

    def get(self, name: str) -> np.ndarray:
        return getattr(self, name)


def compute_window_stats(blocks: np.ndarray, acf_lags: int = 20) -> WindowStats:
    """Estimate every validation statistic separately within each block."""
    b = np.asarray(blocks, dtype=float)
    if b.ndim != 2:
        raise ValueError("blocks must be a 2-D array of shape (n_blocks, horizon).")

    var95, es95, var99, es99 = (np.empty(b.shape[0]) for _ in range(4))
    for i, row in enumerate(b):
        var95[i], es95[i] = var_es(row, 0.95)
        var99[i], es99[i] = var_es(row, 0.99)

    squared_acf = np.vstack([acf(row**2, nlags=acf_lags, fft=True) for row in b])

    return WindowStats(
        horizon=int(b.shape[1]),
        n_blocks=int(b.shape[0]),
        mean=b.mean(axis=1),
        volatility=b.std(axis=1, ddof=1),
        skewness=stats.skew(b, axis=1, bias=False),
        excess_kurtosis=stats.kurtosis(b, axis=1, fisher=True, bias=False),
        q01=np.quantile(b, 0.01, axis=1),
        q05=np.quantile(b, 0.05, axis=1),
        q95=np.quantile(b, 0.95, axis=1),
        q99=np.quantile(b, 0.99, axis=1),
        var95=var95,
        es95=es95,
        var99=var99,
        es99=es99,
        max_drawdown=path_max_drawdowns(b),
        # Estimated within each block and then averaged; blocks are never
        # concatenated before estimation.
        mean_squared_acf=squared_acf.mean(axis=0),
        squared_acf_p05=np.quantile(squared_acf, 0.05, axis=0),
        squared_acf_p95=np.quantile(squared_acf, 0.95, axis=0),
    )
