from __future__ import annotations

import numpy as np
from statsmodels.tsa.stattools import acf


def var_es(returns_pct: np.ndarray, level: float) -> tuple[float, float]:
    """VaR/ES as positive loss magnitudes, where loss = -return."""
    x = np.asarray(returns_pct, dtype=float).reshape(-1)
    losses = -x
    var = float(np.quantile(losses, level))
    tail = losses[losses >= var]
    es = float(np.mean(tail))
    return var, es


def max_drawdown_from_returns(returns_pct: np.ndarray) -> float:
    """Maximum drawdown from percentage log returns."""
    r = np.asarray(returns_pct, dtype=float)
    prices = np.exp(np.cumsum(r / 100.0))
    running_max = np.maximum.accumulate(prices)
    drawdowns = 1.0 - prices / running_max
    return float(np.max(drawdowns))


def path_max_drawdowns(paths_pct: np.ndarray) -> np.ndarray:
    x = np.asarray(paths_pct, dtype=float)
    return np.asarray([max_drawdown_from_returns(path) for path in x], dtype=float)


def historical_rolling_drawdowns(
    returns_pct: np.ndarray,
    horizon: int,
) -> np.ndarray:
    x = np.asarray(returns_pct, dtype=float)
    if x.size < horizon:
        raise ValueError("Not enough historical observations for the requested horizon.")
    return np.asarray(
        [
            max_drawdown_from_returns(x[i : i + horizon])
            for i in range(0, x.size - horizon + 1)
        ],
        dtype=float,
    )


def mean_squared_return_acf(paths_pct: np.ndarray, nlags: int = 20) -> np.ndarray:
    """Mean squared-return ACF across independent paths.

    Paths are deliberately not concatenated.
    """
    x = np.asarray(paths_pct, dtype=float)
    acfs = [acf(path**2, nlags=nlags, fft=True) for path in x]
    return np.mean(np.vstack(acfs), axis=0)
