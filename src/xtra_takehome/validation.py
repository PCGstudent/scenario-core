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
from .windows import WindowStats


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
    # horizon-matched family. Reported, never gated: the upper quantiles of the
    # historical block distribution are not identified (see `extreme_region_checks`).
    real_band: tuple[float, float] | None = None
    synthetic_band: tuple[float, float] | None = None


@dataclass(frozen=True)
class Diagnostic:
    """A reported quantity that is deliberately not a gate."""

    name: str
    real: float
    synthetic: float
    note: str


@dataclass(frozen=True)
class ExtremeRegionCheck:
    """Is the worst year on record plausible under the model?

    Percentile matching in this region is not identified: with overlapping windows
    the top few per cent of historical blocks are one episode repeated. The maximum
    of the record *is* a real event, so the check asks a question the data can
    answer — under the model, how likely is a record of this length to contain
    nothing worse than what was observed?

    `probability_below` is P(model's `n_blocks`-block maximum <= historical maximum).
    A value near 0 means the model produces records that are almost always worse
    than history (too severe); near 1 means it cannot reach the observed extreme
    (not severe enough). Values away from both ends are consistent with the record.
    The threshold is taken over NON-OVERLAPPING blocks, matching the framing.
    """

    statistic: str
    historical_max: float
    annual_exceedance_probability: float
    n_blocks: int
    probability_at_least_one: float
    probability_below: float
    flagged: bool


@dataclass(frozen=True)
class LeaveOutRow:
    """Pooled moments after removing the k most volatile blocks."""

    source: str
    dropped: int
    fraction: float
    volatility: float
    skewness: float
    excess_kurtosis: float


@dataclass(frozen=True)
class MatchedSampleReference:
    """Where the historical statistic sits in the model's own distribution.

    Estimated at the historical sample size, so it answers the only question that
    a single historical realization can support: is the observed value a plausible
    draw from this generator?
    """

    statistic: str
    historical: float
    model_median: float
    model_p05: float
    model_p95: float
    percentile: float
    inside: bool


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

# Scale-free tolerances, shared by both gate families.
THRESHOLDS: dict[str, tuple[str, float]] = {
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
    "drawdown median": ("relative", 0.25),
}

# Two gates cannot use a fixed number, and pretending otherwise was a real defect.
#
# `mean return (pp)`: an absolute tolerance on a daily mean is meaningless without
# a scale. The daily mean is barely identified in 16 years of data, so the gate is
# expressed in standard errors of the historical mean. It is weak by construction,
# and saying so is more useful than a tight-looking number that cannot bind.
MEAN_TOLERANCE_STANDARD_ERRORS = 2.0

# `squared-return ACF MAE`: the sample ACF of squared returns is much smaller in
# 252-day blocks than in the full sample, so carrying the same *absolute* tolerance
# across estimators silently relaxes the gate. Audit showed the consequence: a
# generator with no volatility clustering at all scored 0.0498 against a 0.05
# threshold and passed. The tolerance is therefore declared as a FRACTION of the
# historical scale under whichever estimator is in use. The fraction is the one the
# original absolute tolerance implied on the pooled estimator (0.05 / 0.1271), so
# the strictness is genuinely unchanged and only the units travel.
ACF_TOLERANCE_FRACTION = 0.05 / 0.1271


@dataclass(frozen=True)
class ThresholdContext:
    """Data-derived tolerances for the two gates that need a scale."""

    mean_standard_error: float
    acf_scale: float

    @property
    def mean_threshold(self) -> float:
        return MEAN_TOLERANCE_STANDARD_ERRORS * self.mean_standard_error

    @property
    def acf_threshold(self) -> float:
        return ACF_TOLERANCE_FRACTION * self.acf_scale

    def resolve(self, metric: str) -> tuple[str, float]:
        if metric == "mean return (pp)":
            return "absolute", self.mean_threshold
        if metric == "squared-return ACF MAE":
            return "absolute", self.acf_threshold
        return THRESHOLDS[metric]


def _relative_error(real: float, synthetic: float, floor: float = 1e-8) -> float:
    return abs(synthetic - real) / max(abs(real), floor)


def _gate(
    metric: str,
    real: float,
    synthetic: float,
    context: ThresholdContext,
    real_band: tuple[float, float] | None = None,
    synthetic_band: tuple[float, float] | None = None,
) -> Gate:
    error_type, threshold = context.resolve(metric)
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


def pooled_context(real_returns: np.ndarray, acf_lags: int = 20) -> ThresholdContext:
    x = np.asarray(real_returns, dtype=float).reshape(-1)
    return ThresholdContext(
        mean_standard_error=float(np.std(x, ddof=1) / np.sqrt(x.size)),
        acf_scale=float(np.mean(np.abs(acf(x**2, nlags=acf_lags, fft=True)[1:]))),
    )


def matched_context(
    real_stats: WindowStats, mean_standard_error: float
) -> ThresholdContext:
    """Only the ACF scale is estimator-specific.

    How precisely the historical drift is known depends on the length of the record,
    not on how the record is sliced, so the mean tolerance is carried over unchanged.
    The ACF scale genuinely differs: the same series has a mean absolute squared-return
    autocorrelation of 0.127 estimated on the full sample and 0.046 estimated within
    252-day blocks, and a tolerance stated in absolute units would silently move.
    """
    return ThresholdContext(
        mean_standard_error=mean_standard_error,
        acf_scale=float(np.mean(np.abs(real_stats.mean_squared_acf[1:]))),
    )


# ---------------------------------------------------------------------------
# Family 1: pooled marginal
# ---------------------------------------------------------------------------

_POOLED_QUANTILES = [("q01", 0.01), ("q05", 0.05), ("q95", 0.95), ("q99", 0.99)]


def validate(
    real_returns: pd.Series,
    synthetic_paths: np.ndarray,
    horizon: int = 252,
    acf_lags: int = 20,
) -> tuple[list[Gate], list[Diagnostic], dict[str, np.ndarray | float]]:
    """Pooled marginal check.

    All synthetic observations are pooled and compared with the pooled historical
    sample. This is the right instrument for the unconditional marginal law, but
    the two samples differ in size by a factor of sixty, which matters for any
    statistic that is sample-size dependent. `validate_horizon_matched` estimates
    the same quantities on equal-length blocks and both are reported.
    """
    real = np.asarray(real_returns, dtype=float)
    syn_paths = np.asarray(synthetic_paths, dtype=float)
    syn = syn_paths.reshape(-1)
    context = pooled_context(real, acf_lags=acf_lags)

    gates: list[Gate] = [
        _gate("mean return (pp)", float(np.mean(real)), float(np.mean(syn)), context),
        _gate(
            "volatility",
            float(np.std(real, ddof=1)),
            float(np.std(syn, ddof=1)),
            context,
        ),
        _gate(
            "skewness",
            float(stats.skew(real, bias=False)),
            float(stats.skew(syn, bias=False)),
            context,
        ),
        _gate(
            "excess kurtosis",
            float(stats.kurtosis(real, fisher=True, bias=False)),
            float(stats.kurtosis(syn, fisher=True, bias=False)),
            context,
        ),
    ]

    for name, q in _POOLED_QUANTILES:
        gates.append(
            _gate(name, float(np.quantile(real, q)), float(np.quantile(syn, q)), context)
        )

    for level in (0.95, 0.99):
        real_var, real_es = var_es(real, level)
        syn_var, syn_es = var_es(syn, level)
        pct = int(level * 100)
        gates.append(_gate(f"VaR {pct}%", real_var, syn_var, context))
        gates.append(_gate(f"ES {pct}%", real_es, syn_es, context))

    real_sq_acf = acf(real**2, nlags=acf_lags, fft=True)
    syn_sq_acf = mean_squared_return_acf(syn_paths, nlags=acf_lags)
    acf_mae = float(np.mean(np.abs(real_sq_acf[1:] - syn_sq_acf[1:])))
    gates.append(_gate("squared-return ACF MAE", 0.0, acf_mae, context))

    # Drawdowns are horizon-matched by construction, so they are computed once and
    # reported once. Repeating them in the horizon-matched table would be the same
    # arithmetic printed twice and would pad both scorecards with identical rows.
    hist_dd = historical_rolling_drawdowns(real, horizon=horizon)
    syn_dd = path_max_drawdowns(syn_paths)
    gates.append(
        _gate(
            "drawdown median",
            float(np.median(hist_dd)),
            float(np.median(syn_dd)),
            context,
        )
    )

    # p95 of the historical rolling-drawdown distribution is an upper quantile of
    # overlapping blocks, which the stressed-region analysis shows is not identified.
    # Gating on it would contradict that finding, so it is reported instead.
    diagnostics = [
        Diagnostic(
            name="drawdown p95",
            real=float(np.quantile(hist_dd, 0.95)),
            synthetic=float(np.quantile(syn_dd, 0.95)),
            note=(
                "Upper quantile of overlapping historical windows: not identified, "
                "reported rather than gated."
            ),
        )
    ]

    extras: dict[str, np.ndarray | float] = {
        "real_sq_acf": real_sq_acf,
        "synthetic_sq_acf": syn_sq_acf,
        "historical_drawdowns": hist_dd,
        "synthetic_drawdowns": syn_dd,
        "acf_scale": context.acf_scale,
    }
    return gates, diagnostics, extras


# ---------------------------------------------------------------------------
# Family 2: horizon-matched
# ---------------------------------------------------------------------------

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
    mean_standard_error: float,
) -> list[Gate]:
    """Year-level check: every statistic estimated on equal-length blocks.

    Gate names and tolerances are those of the pooled family; only the estimator
    changes. Where a tolerance needs a scale it is re-derived from the historical
    sample under *this* estimator, which is what keeping the same strictness
    actually requires — carrying the pooled absolute number across would relax the
    ACF gate to the point where a generator with no volatility clustering passes.
    """
    if real_stats.horizon != synthetic_stats.horizon:
        raise ValueError("Horizon-matched validation requires equal block lengths.")

    context = matched_context(real_stats, mean_standard_error)

    gates: list[Gate] = []
    for metric, attribute in _MEDIAN_GATES:
        r = real_stats.get(attribute)
        s = synthetic_stats.get(attribute)
        gates.append(
            _gate(
                metric,
                float(np.median(r)),
                float(np.median(s)),
                context,
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
    gates.append(_gate("squared-return ACF MAE", 0.0, acf_mae, context))
    return gates


def acf_monte_carlo_floor(
    generator,
    horizon: int,
    n_paths: int,
    acf_lags: int,
    seeds: tuple[int, ...] = (901, 902, 903, 904),
) -> tuple[float, float]:
    """Irreducible MAE between two independent simulations of the same model.

    Without this floor a squared-return ACF discrepancy cannot be read: a gate
    failure is only meaningful if it is large relative to the noise two runs of the
    *same* generator produce against each other.
    """
    from .windows import compute_window_stats

    values = []
    for seed in seeds:
        a = compute_window_stats(
            generator.simulate(horizon, n_paths, seed), acf_lags=acf_lags
        ).mean_squared_acf
        b = compute_window_stats(
            generator.simulate(horizon, n_paths, seed + 5000), acf_lags=acf_lags
        ).mean_squared_acf
        values.append(float(np.mean(np.abs(a[1:] - b[1:]))))
    return float(np.median(values)), float(np.max(values))


# ---------------------------------------------------------------------------
# Family 3: the stressed region
# ---------------------------------------------------------------------------

EXTREME_STATISTICS: tuple[tuple[str, str], ...] = (
    ("volatility", "volatility"),
    ("VaR 99%", "var99"),
    ("ES 99%", "es99"),
    ("maximum drawdown", "max_drawdown"),
)

# A record is flagged only when the model essentially cannot produce it, or
# essentially always exceeds it. With so few blocks nothing tighter is honest.
EXTREME_FLAG_BAND = (0.05, 0.95)


def extreme_region_checks(
    non_overlapping_stats: WindowStats,
    synthetic_stats: WindowStats,
) -> list[ExtremeRegionCheck]:
    """Plausibility of the worst year on record, at matched record length.

    The comparison is deliberately not `max(1000 simulated years)` against
    `max(16 observed years)`: the maximum of a heavy-tailed sample grows with the
    sample, so that comparison measures the simulation budget. Instead the model's
    per-year exceedance probability is projected onto a record of the same length
    as the historical one.
    """
    blocks = non_overlapping_stats.n_blocks
    checks: list[ExtremeRegionCheck] = []
    for label, attribute in EXTREME_STATISTICS:
        historical_max = float(np.max(non_overlapping_stats.get(attribute)))
        p = float(np.mean(synthetic_stats.get(attribute) > historical_max))
        probability_below = float((1.0 - p) ** blocks)
        checks.append(
            ExtremeRegionCheck(
                statistic=label,
                historical_max=historical_max,
                annual_exceedance_probability=p,
                n_blocks=blocks,
                probability_at_least_one=1.0 - probability_below,
                probability_below=probability_below,
                flagged=not (
                    EXTREME_FLAG_BAND[0] <= probability_below <= EXTREME_FLAG_BAND[1]
                ),
            )
        )
    return checks


def matched_sample_reference(
    generator,
    real_returns: np.ndarray,
    records_per_seed: int = 100,
    seeds: tuple[int, ...] = (701, 702, 703),
) -> list[MatchedSampleReference]:
    """Simulate whole records of the historical length and locate the observed value.

    This is the decisive check for the pooled moments. A pooled comparison against
    252,000 synthetic observations cannot say whether the historical value is
    surprising; simulating records of the same length as the historical one can.

    Each record is a single continuous simulated path of exactly `len(real_returns)`
    steps, initialized once. An earlier version concatenated sixteen independent
    252-day paths instead, which is not the same object: it reset the conditional
    variance to a fresh historical state every year and so removed any volatility
    episode spanning a year boundary. With an effective persistence of 0.9935 about
    19% of the variance memory survives 252 steps, so that reset is not negligible.
    """
    x = np.asarray(real_returns, dtype=float).reshape(-1)

    samples: dict[str, list[float]] = {
        "volatility": [],
        "skewness": [],
        "excess kurtosis": [],
    }
    for seed in seeds:
        records = generator.simulate(x.size, records_per_seed, seed)
        for record in records:
            samples["volatility"].append(float(np.std(record, ddof=1)))
            samples["skewness"].append(float(stats.skew(record, bias=False)))
            samples["excess kurtosis"].append(
                float(stats.kurtosis(record, fisher=True, bias=False))
            )

    observed = {
        "volatility": float(np.std(x, ddof=1)),
        "skewness": float(stats.skew(x, bias=False)),
        "excess kurtosis": float(stats.kurtosis(x, fisher=True, bias=False)),
    }

    references: list[MatchedSampleReference] = []
    for name, values in samples.items():
        v = np.asarray(values, dtype=float)
        percentile = float(100.0 * np.mean(v <= observed[name]))
        p05, p95 = float(np.quantile(v, 0.05)), float(np.quantile(v, 0.95))
        references.append(
            MatchedSampleReference(
                statistic=name,
                historical=observed[name],
                model_median=float(np.median(v)),
                model_p05=p05,
                model_p95=p95,
                percentile=percentile,
                inside=bool(p05 <= observed[name] <= p95),
            )
        )
    return references


def leave_out_sensitivity(
    source: str,
    blocks: np.ndarray,
    block_volatility: np.ndarray,
    drops: tuple[int, ...] = (0, 1),
) -> list[LeaveOutRow]:
    """Pooled moments after removing the most volatile blocks.

    Applied to the synthetic paths AND to the historical record, because the
    comparison is the point. Heavy-tailed data behaves this way too: without the
    historical row the same table would appear to convict the generator of a
    property the data itself has.
    """
    b = np.asarray(blocks, dtype=float)
    order = np.argsort(np.asarray(block_volatility, dtype=float))[::-1]

    rows: list[LeaveOutRow] = []
    for k in drops:
        if k >= b.shape[0]:
            continue
        kept = b[order[k:]].reshape(-1)
        rows.append(
            LeaveOutRow(
                source=source,
                dropped=k,
                fraction=k / b.shape[0],
                volatility=float(np.std(kept, ddof=1)),
                skewness=float(stats.skew(kept, bias=False)),
                excess_kurtosis=float(stats.kurtosis(kept, fisher=True, bias=False)),
            )
        )
    return rows


def beyond_historical_max_fraction(
    real_stats: WindowStats, synthetic_stats: WindowStats
) -> float:
    """Share of simulated years more volatile than any year in the record.

    Named for what it measures. It is not evidence of a defect on its own: with a
    record of only a few non-overlapping years, a non-trivial share is exactly what
    a correctly calibrated heavy-tailed generator should produce.
    """
    return float(
        np.mean(synthetic_stats.volatility > np.max(real_stats.volatility))
    )
