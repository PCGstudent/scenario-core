from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import acf


@dataclass(frozen=True)
class DiagnosticSummary:
    n: int
    mean: float
    std: float
    skew: float
    excess_kurtosis: float
    min_return: float
    max_return: float
    q01: float
    q05: float
    q95: float
    q99: float
    max_abs_return_acf: float
    mean_abs_squared_acf: float
    student_t_df: float
    student_t_loc: float
    student_t_scale: float
    hill_left: float
    hill_right: float
    hill_k: int


def empirical_moments(returns: pd.Series) -> dict[str, float]:
    x = np.asarray(returns, dtype=float)
    return {
        "n": int(x.size),
        "mean": float(np.mean(x)),
        "std": float(np.std(x, ddof=1)),
        "skew": float(stats.skew(x, bias=False)),
        "excess_kurtosis": float(stats.kurtosis(x, fisher=True, bias=False)),
        "min": float(np.min(x)),
        "max": float(np.max(x)),
        "q01": float(np.quantile(x, 0.01)),
        "q05": float(np.quantile(x, 0.05)),
        "q95": float(np.quantile(x, 0.95)),
        "q99": float(np.quantile(x, 0.99)),
    }


def acf_values(returns: pd.Series, nlags: int = 20) -> tuple[np.ndarray, np.ndarray]:
    x = np.asarray(returns, dtype=float)
    r_acf = acf(x, nlags=nlags, fft=True)
    sq_acf = acf(x**2, nlags=nlags, fft=True)
    return r_acf, sq_acf


def fit_student_t(returns: pd.Series) -> tuple[float, float, float]:
    df, loc, scale = stats.t.fit(np.asarray(returns, dtype=float))
    return float(df), float(loc), float(scale)


def student_t_qq(
    returns: pd.Series,
) -> tuple[np.ndarray, np.ndarray, tuple[float, float, float]]:
    x = np.sort(np.asarray(returns, dtype=float))
    n = x.size
    probs = (np.arange(1, n + 1) - 0.5) / n
    params = fit_student_t(returns)
    theoretical = stats.t.ppf(probs, *params)
    return theoretical, x, params


def hill_estimator(values: np.ndarray, k: int) -> float:
    """Hill estimate of the tail index alpha from the k largest positive values.

    Smaller alpha means a heavier tail; alpha <= 2 implies infinite variance and
    alpha <= 4 implies infinite kurtosis for the marginal law.
    """
    x = np.asarray(values, dtype=float)
    x = np.sort(x[x > 0.0])[::-1]
    if k < 1 or k >= x.size:
        raise ValueError("k must satisfy 1 <= k < number of positive observations.")
    return float(1.0 / np.mean(np.log(x[:k] / x[k])))


def hill_profile(
    returns: pd.Series,
    k_values: tuple[int, ...] = (50, 100, 150, 200, 300, 400),
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Hill tail-index profiles for the loss (left) and gain (right) tails."""
    x = np.asarray(returns, dtype=float)
    ks = np.asarray(k_values, dtype=int)
    left = np.asarray([hill_estimator(-x, k) for k in ks], dtype=float)
    right = np.asarray([hill_estimator(x, k) for k in ks], dtype=float)
    return ks, left, right


def mean_excess(
    returns: pd.Series,
    q_min: float = 0.90,
    q_max: float = 0.99,
    n_thresholds: int = 30,
) -> tuple[np.ndarray, np.ndarray]:
    """Mean excess function for losses, L=-return."""
    losses = -np.asarray(returns, dtype=float)
    qs = np.linspace(q_min, q_max, n_thresholds)
    thresholds = np.quantile(losses, qs)
    excess = []
    for u in thresholds:
        exceedances = losses[losses > u] - u
        excess.append(np.mean(exceedances) if exceedances.size else np.nan)
    return thresholds, np.asarray(excess, dtype=float)


HILL_REFERENCE_K = 100


def summarize(returns: pd.Series, nlags: int = 20) -> DiagnosticSummary:
    m = empirical_moments(returns)
    r_acf, sq_acf = acf_values(returns, nlags=nlags)
    df, loc, scale = fit_student_t(returns)
    x = np.asarray(returns, dtype=float)
    return DiagnosticSummary(
        n=int(m["n"]),
        mean=m["mean"],
        std=m["std"],
        skew=m["skew"],
        excess_kurtosis=m["excess_kurtosis"],
        min_return=m["min"],
        max_return=m["max"],
        q01=m["q01"],
        q05=m["q05"],
        q95=m["q95"],
        q99=m["q99"],
        max_abs_return_acf=float(np.max(np.abs(r_acf[1:]))),
        mean_abs_squared_acf=float(np.mean(np.abs(sq_acf[1:]))),
        student_t_df=df,
        student_t_loc=loc,
        student_t_scale=scale,
        hill_left=hill_estimator(-x, HILL_REFERENCE_K),
        hill_right=hill_estimator(x, HILL_REFERENCE_K),
        hill_k=HILL_REFERENCE_K,
    )
