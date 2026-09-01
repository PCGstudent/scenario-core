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


def summarize(returns: pd.Series, nlags: int = 20) -> DiagnosticSummary:
    m = empirical_moments(returns)
    r_acf, sq_acf = acf_values(returns, nlags=nlags)
    df, loc, scale = fit_student_t(returns)
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
    )
