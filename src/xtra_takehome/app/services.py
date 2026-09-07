"""Orchestration between the modelling core and the interface.

Everything here is a plain function over plain data, with no Streamlit import,
so the same layer can sit behind a FastAPI route later. Caching is applied by
the caller; these functions are deterministic given their arguments, which is
what makes them safe to cache.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from ..challenger import GjrSkewTGenerator, GjrSkewTParams
from ..config import Config
from ..data import fetch_close, log_returns_pct
from ..diagnostics import DiagnosticSummary, acf_values, hill_profile, mean_excess, summarize
from ..validation import (
    Diagnostic,
    ExtremeRegionCheck,
    Gate,
    MatchedSampleReference,
    acf_monte_carlo_floor,
    beyond_historical_max_fraction,
    extreme_region_checks,
    matched_sample_reference,
    pooled_context,
    validate,
    validate_horizon_matched,
)
from ..windows import (
    WindowStats,
    compute_window_stats,
    non_overlapping_block_count,
    non_overlapping_blocks,
    rolling_blocks,
)

# The lab lets the user pick a horizon and a path count. The validation suite
# does not: its tolerances were derived for this configuration, and running a
# 252-day acceptance test against 30-day scenarios would produce PASS/FAIL marks
# that mean nothing. Validation therefore always runs here.
CANONICAL = Config()


@dataclass
class MarketData:
    close: pd.Series
    returns: pd.Series

    @property
    def latest_price(self) -> float:
        return float(self.close.iloc[-1])

    @property
    def latest_date(self) -> pd.Timestamp:
        return self.close.index[-1]

    @property
    def first_date(self) -> pd.Timestamp:
        return self.close.index[0]

    @property
    def n_returns(self) -> int:
        return int(self.returns.size)


@dataclass
class FittedModel:
    generator: GjrSkewTGenerator
    params: GjrSkewTParams
    fit_summary: str

    @property
    def latest_conditional_volatility(self) -> float:
        """Today's conditional volatility in percent per day."""
        _, variance = self.generator.latest_state
        return float(np.sqrt(variance))

    @property
    def implied_unconditional_volatility(self) -> float:
        return float(np.sqrt(self.params.implied_unconditional_variance))


@dataclass
class HistoricalDiagnostics:
    summary: DiagnosticSummary
    return_acf: np.ndarray
    squared_acf: np.ndarray
    hill_k: np.ndarray
    hill_left: np.ndarray
    hill_right: np.ndarray
    mean_excess_thresholds: np.ndarray
    mean_excess_values: np.ndarray
    rolling_volatility: pd.Series


@dataclass
class ValidationResults:
    """The repository's own validation, run at the canonical configuration."""

    pooled_gates: list[Gate]
    pooled_diagnostics: list[Diagnostic]
    matched_gates: list[Gate]
    extremes: list[ExtremeRegionCheck]
    references: list[MatchedSampleReference]
    real_stats: WindowStats
    synthetic_stats: WindowStats
    acf_floor_median: float
    acf_floor_max: float
    beyond_max_fraction: float
    synthetic_returns: np.ndarray
    """The canonical synthetic set the marks were computed from.

    Kept so the interface plots exactly the paths that were validated, rather
    than re-simulating and trusting that the two stay identical.
    """
    extras: dict = field(default_factory=dict)

    @property
    def pooled_passed(self) -> int:
        return sum(g.passed for g in self.pooled_gates)

    @property
    def matched_passed(self) -> int:
        return sum(g.passed for g in self.matched_gates)


@dataclass
class ScenarioSet:
    """A generated set of scenarios plus everything needed to reproduce it."""

    returns: np.ndarray
    variances: np.ndarray
    n_paths: int
    horizon: int
    seed: int
    initial_state: str
    initial_price: float
    runtime_seconds: float

    @property
    def conditional_volatility(self) -> np.ndarray:
        """Conditional volatility paths, percent per day."""
        return np.sqrt(self.variances)

    @property
    def provenance(self) -> dict:
        return {
            "n_paths": self.n_paths,
            "horizon_trading_days": self.horizon,
            "seed": self.seed,
            "initial_state": self.initial_state,
            "initial_price": self.initial_price,
        }


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_market_data(config: Config | None = None) -> MarketData:
    cfg = config or CANONICAL
    close = fetch_close(cfg.ticker, cfg.start, cfg.end)
    return MarketData(close=close, returns=log_returns_pct(close))


def fit_model(returns: pd.Series) -> FittedModel:
    generator = GjrSkewTGenerator().fit(returns)
    assert generator.params_ is not None and generator.fit_summary_ is not None
    return FittedModel(
        generator=generator,
        params=generator.params_,
        fit_summary=generator.fit_summary_,
    )


def compute_diagnostics(
    returns: pd.Series, acf_lags: int = CANONICAL.max_acf_lag
) -> HistoricalDiagnostics:
    r_acf, sq_acf = acf_values(returns, nlags=acf_lags)
    ks, left, right = hill_profile(returns)
    thresholds, excess = mean_excess(returns)
    rolling = returns.rolling(21).std() * np.sqrt(252.0)
    return HistoricalDiagnostics(
        summary=summarize(returns, nlags=acf_lags),
        return_acf=r_acf,
        squared_acf=sq_acf,
        hill_k=ks,
        hill_left=left,
        hill_right=right,
        mean_excess_thresholds=thresholds,
        mean_excess_values=excess,
        rolling_volatility=rolling.dropna(),
    )


# ---------------------------------------------------------------------------
# Generation
# ---------------------------------------------------------------------------


def generate_scenarios(
    model: FittedModel,
    n_paths: int,
    horizon: int,
    seed: int,
    initial_state: str | tuple[float, float],
    initial_price: float,
) -> ScenarioSet:
    """Run the fitted generator. Deterministic given its arguments."""
    import time

    started = time.perf_counter()
    returns, variances = model.generator.simulate(
        n_steps=horizon,
        n_paths=n_paths,
        seed=seed,
        initial_state=initial_state,
        return_variance=True,
    )
    elapsed = time.perf_counter() - started

    label = initial_state if isinstance(initial_state, str) else "explicit"
    return ScenarioSet(
        returns=returns,
        variances=variances,
        n_paths=int(n_paths),
        horizon=int(horizon),
        seed=int(seed),
        initial_state=label,
        initial_price=float(initial_price),
        runtime_seconds=float(elapsed),
    )


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def run_validation(
    model: FittedModel, returns: pd.Series, config: Config | None = None
) -> ValidationResults:
    """Run the repository's validation suite unchanged, at the canonical config.

    This is the same code path that produces `reports/validation_report.md`, so
    the marks shown in the app are the marks in the committed report -- not a
    simplified re-implementation that might disagree with it.
    """
    cfg = config or CANONICAL
    returns_array = returns.to_numpy()

    synthetic = model.generator.simulate(cfg.horizon, cfg.n_paths, cfg.seed)
    pooled_gates, pooled_diagnostics, extras = validate(
        returns, synthetic, horizon=cfg.horizon, acf_lags=cfg.max_acf_lag
    )

    real_stats = compute_window_stats(
        rolling_blocks(returns_array, cfg.horizon), acf_lags=cfg.max_acf_lag
    )
    synthetic_stats = compute_window_stats(synthetic, acf_lags=cfg.max_acf_lag)
    mean_se = pooled_context(returns_array, acf_lags=cfg.max_acf_lag).mean_standard_error
    matched_gates = validate_horizon_matched(real_stats, synthetic_stats, mean_se)

    disjoint_stats = compute_window_stats(
        non_overlapping_blocks(returns_array, cfg.horizon), acf_lags=cfg.max_acf_lag
    )
    extremes = extreme_region_checks(disjoint_stats, synthetic_stats)
    references = matched_sample_reference(model.generator, returns_array)
    floor_median, floor_max = acf_monte_carlo_floor(
        model.generator, cfg.horizon, cfg.n_paths, cfg.max_acf_lag
    )

    return ValidationResults(
        pooled_gates=pooled_gates,
        pooled_diagnostics=pooled_diagnostics,
        matched_gates=matched_gates,
        extremes=extremes,
        references=references,
        real_stats=real_stats,
        synthetic_stats=synthetic_stats,
        acf_floor_median=floor_median,
        acf_floor_max=floor_max,
        beyond_max_fraction=beyond_historical_max_fraction(real_stats, synthetic_stats),
        synthetic_returns=synthetic,
        extras=extras,
    )


def independent_years(n_returns: int, horizon: int = CANONICAL.horizon) -> int:
    """Non-overlapping blocks in the record. An upper bound on independent draws."""
    return non_overlapping_block_count(n_returns, horizon)
