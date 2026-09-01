from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats
from statsmodels.tsa.stattools import acf

from .metrics import (
    historical_rolling_drawdowns,
    mean_squared_return_acf,
    path_max_drawdowns,
    var_es,
)
from .windows import WindowStats, independent_block_count


@dataclass(frozen=True)
class Gate:
    metric: str
    real: float
    synthetic: float
    error: float
    threshold: float
    error_type: str
    passed: bool
    # Dispersion of the underlying statistic across blocks, populated only by the
    # horizon-matched family. Reported, never gated: see `exceedance_checks` for
    # why the upper quantiles of the historical block distribution are not a
    # legitimate gate target.
    real_band: tuple[float, float] | None = None
    synthetic_band: tuple[float, float] | None = None


@dataclass(frozen=True)
class ExceedanceCheck:
    """Frequency check for years at least as severe as the worst observed year.

    Percentile-matching in the upper tail of the historical block distribution is
    not identified: with overlapping windows the top few per cent of blocks are one
    episode repeated. What *is* identified is how often history produced a year at
    least as bad as its worst one, so the check compares the model-implied annual
    exceedance rate with the exact Poisson interval for having observed one such
    year in `independent_years` independent years.
    """

    statistic: str
    historical_max: float
    synthetic_exceedance_probability: float
    independent_years: int
    implied_expected_count: float
    lower_count: float
    upper_count: float
    passed: bool


@dataclass(frozen=True)
class LeaveOutRow:
    """Pooled moments after removing the k most volatile synthetic paths."""

    dropped: int
    fraction: float
    volatility: float
    skewness: float
    excess_kurtosis: float


def explosive_path_sensitivity(
    synthetic_paths: np.ndarray,
    block_volatility: np.ndarray,
    drops: tuple[int, ...] = (0, 1, 5, 10),
) -> list[LeaveOutRow]:
    """How concentrated are the pooled moments in a handful of paths?

    This is a *diagnostic*, never a remedy. Removing paths after seeing the result
    would be data snooping; the point is only to measure how much of the pooled
    failure is carried by a negligible fraction of the simulation, which
    distinguishes a systematically miscalibrated generator from one whose
    unconditional moments are hostage to a near-integrated variance recursion.
    """
    paths = np.asarray(synthetic_paths, dtype=float)
    order = np.argsort(np.asarray(block_volatility, dtype=float))[::-1]

    rows: list[LeaveOutRow] = []
    for k in drops:
        if k >= paths.shape[0]:
            continue
        kept = paths[order[k:]].reshape(-1)
        rows.append(
            LeaveOutRow(
                dropped=k,
                fraction=k / paths.shape[0],
                volatility=float(np.std(kept, ddof=1)),
                skewness=float(stats.skew(kept, bias=False)),
                excess_kurtosis=float(stats.kurtosis(kept, fisher=True, bias=False)),
            )
        )
    return rows


def _relative_error(real: float, synthetic: float, floor: float = 1e-8) -> float:
    return abs(synthetic - real) / max(abs(real), floor)


# One declared threshold per metric, shared by BOTH gate families. The pooled and
# horizon-matched families differ only in how the statistic is estimated, never in
# the tolerance it must meet, so a change of estimator can never be mistaken for a
# relaxation of the acceptance criteria.
THRESHOLDS: dict[str, tuple[str, float]] = {
    "mean return (pp)": ("absolute", 0.10),
    "volatility": ("relative", 0.10),
    "skewness": ("absolute", 0.50),
    "excess kurtosis": ("absolute", 2.00),
    "q01": ("relative", 0.20),
    "q05": ("relative", 0.15),
    "q95": ("relative", 0.15),
    "q99": ("relative", 0.20),
    "VaR 95%": ("relative", 0.15),
    "ES 95%": ("relative", 0.20),
    "VaR 99%": ("relative", 0.20),
    "ES 99%": ("relative", 0.25),
    "squared-return ACF MAE": ("absolute", 0.05),
    "drawdown median": ("relative", 0.25),
    "drawdown p95": ("relative", 0.30),
}


def _gate(
    metric: str,
    real: float,
    synthetic: float,
    real_band: tuple[float, float] | None = None,
    synthetic_band: tuple[float, float] | None = None,
) -> Gate:
    """Build a gate from the shared threshold table."""
    error_type, threshold = THRESHOLDS[metric]
    error = (
        _relative_error(real, synthetic)
        if error_type == "relative"
        else abs(synthetic - real)
    )
    return Gate(
        metric=metric,
        real=real,
        synthetic=synthetic,
        error=error,
        threshold=threshold,
        error_type=error_type,
        passed=error <= threshold,
        real_band=real_band,
        synthetic_band=synthetic_band,
    )


POISSON_INTERVAL_COVERAGE = 0.90


def poisson_count_interval(
    observed: int = 1,
    coverage: float = POISSON_INTERVAL_COVERAGE,
) -> tuple[float, float]:
    """Exact (Garwood) interval for a Poisson mean given `observed` events."""
    tail = (1.0 - coverage) / 2.0
    lower = 0.0 if observed == 0 else float(stats.chi2.ppf(tail, 2 * observed) / 2.0)
    upper = float(stats.chi2.ppf(1.0 - tail, 2 * observed + 2) / 2.0)
    return lower, upper


def validate(
    real_returns: pd.Series,
    synthetic_paths: np.ndarray,
    horizon: int = 252,
    acf_lags: int = 20,
) -> tuple[list[Gate], dict[str, np.ndarray | float]]:
    """Pooled marginal check.

    Every synthetic observation is pooled into one sample and compared with the
    pooled historical sample. This is the right instrument for the unconditional
    marginal law, but three of its gates are estimator-sensitive because the two
    samples have very different sizes (252,000 vs ~4,158): squared-return ACF and
    excess kurtosis are strongly sample-size dependent, and pooled volatility is
    sensitive to rare explosive paths. `validate_horizon_matched` estimates the
    same quantities on equal-length blocks; the two are reported side by side.
    """
    real = np.asarray(real_returns, dtype=float)
    syn_paths = np.asarray(synthetic_paths, dtype=float)
    syn = syn_paths.reshape(-1)

    gates: list[Gate] = [
        _gate("mean return (pp)", float(np.mean(real)), float(np.mean(syn))),
        _gate(
            "volatility",
            float(np.std(real, ddof=1)),
            float(np.std(syn, ddof=1)),
        ),
        _gate(
            "skewness",
            float(stats.skew(real, bias=False)),
            float(stats.skew(syn, bias=False)),
        ),
        _gate(
            "excess kurtosis",
            float(stats.kurtosis(real, fisher=True, bias=False)),
            float(stats.kurtosis(syn, fisher=True, bias=False)),
        ),
    ]

    for name, q in [("q01", 0.01), ("q05", 0.05), ("q95", 0.95), ("q99", 0.99)]:
        gates.append(
            _gate(name, float(np.quantile(real, q)), float(np.quantile(syn, q)))
        )

    # Risk measures: positive loss magnitudes.
    for level in (0.95, 0.99):
        real_var, real_es = var_es(real, level)
        syn_var, syn_es = var_es(syn, level)
        pct = int(level * 100)
        gates.append(_gate(f"VaR {pct}%", real_var, syn_var))
        gates.append(_gate(f"ES {pct}%", real_es, syn_es))

    # Volatility clustering. The historical ACF is estimated on the full sample
    # while the synthetic ACF is averaged over `horizon`-length paths, so this
    # particular gate compares two different estimators; the horizon-matched
    # family repeats it like-for-like.
    real_sq_acf = acf(real**2, nlags=acf_lags, fft=True)
    syn_sq_acf = mean_squared_return_acf(syn_paths, nlags=acf_lags)
    acf_mae = float(np.mean(np.abs(real_sq_acf[1:] - syn_sq_acf[1:])))
    gates.append(_gate("squared-return ACF MAE", 0.0, acf_mae))

    # Like-for-like 252-day maximum drawdowns.
    hist_dd = historical_rolling_drawdowns(real, horizon=horizon)
    syn_dd = path_max_drawdowns(syn_paths)
    for label, q in [("drawdown median", 0.50), ("drawdown p95", 0.95)]:
        gates.append(
            _gate(label, float(np.quantile(hist_dd, q)), float(np.quantile(syn_dd, q)))
        )

    extras: dict[str, np.ndarray | float] = {
        "real_sq_acf": real_sq_acf,
        "synthetic_sq_acf": syn_sq_acf,
        "historical_drawdowns": hist_dd,
        "synthetic_drawdowns": syn_dd,
    }
    return gates, extras


_MEDIAN_GATES: tuple[tuple[str, str], ...] = (
    ("mean return (pp)", "mean"),
    ("volatility", "volatility"),
    ("skewness", "skewness"),
    ("excess kurtosis", "excess_kurtosis"),
    ("q01", "q01"),
    ("q05", "q05"),
    ("q95", "q95"),
    ("q99", "q99"),
    ("VaR 95%", "var95"),
    ("ES 95%", "es95"),
    ("VaR 99%", "var99"),
    ("ES 99%", "es99"),
)


def _band(x: np.ndarray) -> tuple[float, float]:
    return float(np.quantile(x, 0.05)), float(np.quantile(x, 0.95))


def validate_horizon_matched(
    real_stats: WindowStats,
    synthetic_stats: WindowStats,
) -> list[Gate]:
    """Year-level check: every statistic estimated on equal-length blocks.

    Gate names and thresholds are exactly those of the pooled family; only the
    estimator changes. Each gate compares the *median* of the statistic across
    blocks, which is the typical year. The 5th-95th percentile bands are carried
    for reporting but deliberately not gated, because the upper tail of the
    historical block distribution is not identified (see `exceedance_checks`).
    """
    if real_stats.horizon != synthetic_stats.horizon:
        raise ValueError("Horizon-matched validation requires equal block lengths.")

    gates: list[Gate] = []
    for metric, attribute in _MEDIAN_GATES:
        r = real_stats.get(attribute)
        s = synthetic_stats.get(attribute)
        gates.append(
            _gate(
                metric,
                float(np.median(r)),
                float(np.median(s)),
                real_band=_band(r),
                synthetic_band=_band(s),
            )
        )

    acf_mae = float(
        np.mean(
            np.abs(
                real_stats.mean_squared_acf[1:] - synthetic_stats.mean_squared_acf[1:]
            )
        )
    )
    gates.append(_gate("squared-return ACF MAE", 0.0, acf_mae))

    for label, q in [("drawdown median", 0.50), ("drawdown p95", 0.95)]:
        gates.append(
            _gate(
                label,
                float(np.quantile(real_stats.max_drawdown, q)),
                float(np.quantile(synthetic_stats.max_drawdown, q)),
            )
        )
    return gates


EXCEEDANCE_STATISTICS: tuple[tuple[str, str], ...] = (
    ("volatility", "volatility"),
    ("VaR 99%", "var99"),
    ("ES 99%", "es99"),
    ("maximum drawdown", "max_drawdown"),
)


def exceedance_checks(
    real_stats: WindowStats,
    synthetic_stats: WindowStats,
    n_observations: int,
) -> list[ExceedanceCheck]:
    """How often does the model produce a year at least as severe as the worst observed one?

    This replaces percentile matching in the stressed region. With overlapping
    historical windows the top few per cent of blocks are a single episode repeated,
    so a historical p95 is not an estimate of a 95th percentile. The worst observed
    year, by contrast, is a real event that happened once in
    `independent_block_count` independent years, and the model-implied annual
    exceedance rate can be checked against the exact Poisson interval for one event.
    """
    years = independent_block_count(n_observations, real_stats.horizon)
    lower, upper = poisson_count_interval(observed=1)

    checks: list[ExceedanceCheck] = []
    for label, attribute in EXCEEDANCE_STATISTICS:
        historical_max = float(np.max(real_stats.get(attribute)))
        probability = float(np.mean(synthetic_stats.get(attribute) > historical_max))
        expected = probability * years
        checks.append(
            ExceedanceCheck(
                statistic=label,
                historical_max=historical_max,
                synthetic_exceedance_probability=probability,
                independent_years=years,
                implied_expected_count=expected,
                lower_count=lower,
                upper_count=upper,
                passed=bool(lower <= expected <= upper),
            )
        )
    return checks
