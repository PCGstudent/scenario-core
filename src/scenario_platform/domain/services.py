"""Pure application services over the existing quantitative core.

``fit``, ``simulate``, ``validate`` and ``risk`` are the whole Phase-1 domain
surface (architecture plan Section 7.1). Every one of them is a thin adapter
over ``xtra_takehome``: none re-implements a GJR-GARCH equation, a skew-t
moment, an RNG rule, a threshold, a sign convention, or a validation
estimator. They must never access AWS, never read deployment environment
variables, never import Streamlit, and never import ``boto3``/``botocore``
(AGENTS.md invariant 28, enforced by ``tests/test_no_aws_in_core.py``).

``validate`` in particular takes ``(real_returns, artifact, config)`` and
*not* ``(real_returns, simulated_array)`` -- see Section 7.2 of the
architecture plan and ``reports.ValidationReport``'s docstring for why:
``matched_sample_reference`` and ``acf_monte_carlo_floor`` both re-simulate
internally from the generator, which only ``artifact`` (not a finished
array) makes possible.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import numpy as np
import numpy.typing as npt
import pandas as pd

from xtra_takehome import diagnostics as _diagnostics
from xtra_takehome import metrics as _metrics
from xtra_takehome import windows as _windows
from xtra_takehome.challenger import GjrSkewTGenerator
from xtra_takehome.validation import (
    acf_monte_carlo_floor,
    beyond_historical_max_fraction,
    extreme_region_checks,
    leave_out_sensitivity,
    matched_sample_reference,
    pooled_context,
    validate_horizon_matched,
)
from xtra_takehome.validation import (
    validate as _validate_core,
)

from . import identity as _identity
from .artifacts import ModelArtifact, Provenance, StructuralDiagnostics
from .reports import RiskReport, ScenarioSet, ValidationReport
from .requests import FitConfig, RiskConfig, ScenarioRequest, ValidationConfig


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _reconstruct_generator(artifact: ModelArtifact) -> GjrSkewTGenerator:
    """Rebuild a working generator from an artifact's stored fitted state.

    Assigns ``GjrSkewTGenerator``'s already-existing internal attributes
    (``params_``, ``_residuals``, ``_variances``) directly, rather than
    calling ``.fit()`` again -- the identical technique
    ``tests/test_challenger.py::_fitted_like_generator`` already uses to
    build a fitted-like generator for testing without a live optimiser run.
    No formula, RNG rule, or initial-state rule is reimplemented: this
    function only feeds an artifact's own already-fitted values into the
    constructor path the quantitative core already provides for exactly
    this purpose.

    The artifact's arrays are read-only (``ModelArtifact.__post_init__``);
    they are handed to the generator without copying, so any code that
    later tried to mutate ``generator._residuals`` in place would raise
    immediately rather than silently corrupting the artifact it came from.
    """
    generator = GjrSkewTGenerator()
    generator.params_ = artifact.params
    generator._residuals = artifact.fitted_residuals
    generator._variances = artifact.fitted_variances
    return generator


def fit(returns: pd.Series, config: FitConfig) -> ModelArtifact:
    """Fit the existing GJR-skew-t generator and wrap it as a ModelArtifact.

    Delegates the entire fit to ``GjrSkewTGenerator.fit`` -- no formula,
    threshold, or optimiser call is reproduced here. ``config.dataset`` must
    already be the identity of the exact series ``returns`` was derived from
    (``artifacts.build_dataset_ref`` on the underlying close-price series);
    this function does not re-fetch or re-identify anything.
    """
    generator = GjrSkewTGenerator().fit(returns)
    assert generator.params_ is not None  # narrows Optional for the type checker
    params = generator.params_
    fitted_residuals, fitted_variances = generator.fitted_states

    implied_return_tail_index = params.implied_return_tail_index
    fourth_moment_coefficient = params.fourth_moment_coefficient
    # The loss-side Hill estimate at k=100 -- the exact "non-circular
    # comparator" the README and validation_report.md quote as 2.94; not a
    # new tail-index estimator, just the existing one called with the
    # existing reference k.
    hill_tail_index = _diagnostics.hill_estimator(
        -np.asarray(returns, dtype=float), _diagnostics.HILL_REFERENCE_K
    )

    diagnostics = StructuralDiagnostics(
        effective_persistence=params.effective_persistence,
        fourth_moment_coefficient=fourth_moment_coefficient,
        implied_unconditional_variance=params.implied_unconditional_variance,
        implied_return_tail_index=implied_return_tail_index,
        hill_tail_index=hill_tail_index,
        finite_second_moment=implied_return_tail_index > 2.0,
        finite_third_moment=implied_return_tail_index > 3.0,
        finite_fourth_moment=(
            implied_return_tail_index > 4.0 and fourth_moment_coefficient < 1.0
        ),
    )

    provenance = Provenance(
        dataset_id=config.dataset.dataset_id,
        dataset_uri=config.dataset.uri,
        calibration_window_start=config.dataset.start,
        calibration_window_end=config.dataset.end,
        calibration_timestamp=_utc_now_iso(),
    )

    artifact_id = _identity.compute_artifact_id(
        family=config.family,
        threshold_set_version=config.threshold_set_version,
        policy_set_version=config.policy_set_version,
        dataset_id=config.dataset.dataset_id,
        calibration_start=config.dataset.start,
        calibration_end=config.dataset.end,
        params=params,
        fitted_residuals=fitted_residuals,
        fitted_variances=fitted_variances,
    )

    return ModelArtifact(
        schema_version=_identity.ARTIFACT_SCHEMA,
        model_version=config.model_version,
        artifact_id=artifact_id,
        family=config.family,
        params=params,
        fitted_residuals=fitted_residuals,
        fitted_variances=fitted_variances,
        diagnostics=diagnostics,
        provenance=provenance,
        threshold_set_version=config.threshold_set_version,
        policy_set_version=config.policy_set_version,
    )


def simulate(artifact: ModelArtifact, request: ScenarioRequest) -> ScenarioSet:
    """Simulate scenario paths from a fitted artifact.

    Reconstructs the generator from the artifact's own fitted state and
    calls ``GjrSkewTGenerator.simulate`` with the request's fields passed
    straight through -- ``initial_state``, ``seed`` and ``return_variance``
    keep exactly the semantics the quantitative core already defines for
    them (``historical_mix`` / ``latest`` / explicit ``(residual,
    variance)``; ``SeedSequence(seed).spawn(2)`` internally).

    Verifies the artifact's identity before using it (fail closed, Section
    8.3), even though an artifact produced by :func:`fit` in this same
    process is already correct by construction -- the check costs
    microseconds and catches a caller that assembled a ``ModelArtifact`` by
    hand from mismatched pieces.
    """
    if request.rng_scheme != "single":
        raise NotImplementedError(
            f"rng_scheme={request.rng_scheme!r} is a reserved value with no "
            "executable behaviour in Phase 1 (architecture plan Section 21.4: "
            "sharding changes the RNG stream and must always be an explicit, "
            "versioned opt-in, never a silent default). Only 'single' is "
            "implemented."
        )
    artifact.verify_identity()
    generator = _reconstruct_generator(artifact)

    result = generator.simulate(
        n_steps=request.horizon,
        n_paths=request.n_paths,
        seed=request.seed,
        initial_state=request.initial_state,
        return_variance=request.return_variance,
    )
    if request.return_variance:
        returns, variances = result
    else:
        returns, variances = result, None

    return ScenarioSet(
        artifact_id=artifact.artifact_id,
        request=request,
        returns=returns,
        variances=variances,
    )


def validate(
    real_returns: pd.Series,
    artifact: ModelArtifact,
    config: ValidationConfig,
) -> ValidationReport:
    """Run the repository's own three-family validation against one artifact.

    Reproduces ``xtra_takehome.__main__``'s validation section exactly,
    function for function: pooled marginal, horizon-matched, the
    matched-length reference, the squared-return-ACF Monte Carlo floor,
    extreme-region plausibility, and leave-out sensitivity on both sides.
    Nothing here re-derives a gate, a threshold, or an estimator -- every
    number comes from ``xtra_takehome.validation``/``xtra_takehome.windows``
    unchanged.
    """
    artifact.verify_identity()
    generator = _reconstruct_generator(artifact)
    real_array = np.asarray(real_returns, dtype=float)

    # `return_variance` is not passed, so this is always an array (never the
    # `(returns, variances)` tuple form) -- the cast reflects that call-site
    # fact for mypy, it does not change what `simulate` returns.
    synthetic = cast(
        npt.NDArray[np.float64],
        generator.simulate(config.horizon, config.n_paths, config.seed),
    )

    pooled_gates, pooled_diagnostics, _extras = _validate_core(
        real_returns, synthetic, horizon=config.horizon, acf_lags=config.max_acf_lag
    )

    real_stats = _windows.compute_window_stats(
        _windows.rolling_blocks(real_array, config.horizon), acf_lags=config.max_acf_lag
    )
    synthetic_stats = _windows.compute_window_stats(synthetic, acf_lags=config.max_acf_lag)
    mean_standard_error = pooled_context(
        real_array, acf_lags=config.max_acf_lag
    ).mean_standard_error
    matched_gates = validate_horizon_matched(
        real_stats, synthetic_stats, mean_standard_error
    )

    disjoint_blocks = _windows.non_overlapping_blocks(real_array, config.horizon)
    disjoint_stats = _windows.compute_window_stats(
        disjoint_blocks, acf_lags=config.max_acf_lag
    )
    extremes = extreme_region_checks(disjoint_stats, synthetic_stats)

    references = matched_sample_reference(
        generator,
        real_array,
        records_per_seed=config.records_per_seed,
        seeds=config.reference_seeds,
    )
    acf_floor_median, acf_floor_max = acf_monte_carlo_floor(
        generator,
        config.horizon,
        config.n_paths,
        config.max_acf_lag,
        seeds=config.acf_floor_seeds,
    )

    leave_out = leave_out_sensitivity(
        "synthetic", synthetic, synthetic_stats.volatility, drops=config.leave_out_drops
    ) + leave_out_sensitivity(
        "historical",
        disjoint_blocks,
        disjoint_stats.volatility,
        drops=config.leave_out_drops,
    )
    beyond_max_fraction = beyond_historical_max_fraction(real_stats, synthetic_stats)

    return ValidationReport(
        artifact_id=artifact.artifact_id,
        pooled_gates=pooled_gates,
        pooled_diagnostics=pooled_diagnostics,
        matched_gates=matched_gates,
        extremes=extremes,
        references=references,
        real_stats=real_stats,
        synthetic_stats=synthetic_stats,
        acf_floor_median=acf_floor_median,
        acf_floor_max=acf_floor_max,
        beyond_max_fraction=beyond_max_fraction,
        leave_out=leave_out,
    )


def risk(scenarios: ScenarioSet, config: RiskConfig) -> RiskReport:
    """Compute a pooled risk report over a generated scenario set.

    Uses ``xtra_takehome.metrics.var_es`` verbatim -- loss = -return, both
    VaR and ES reported as positive loss magnitudes, ES >= VaR by
    construction -- and ``path_max_drawdowns`` for the drawdown summary.
    Neither convention is re-derived here.
    """
    flat_returns = scenarios.returns.reshape(-1)
    var_es_by_level = {
        level: _metrics.var_es(flat_returns, level) for level in config.levels
    }
    drawdowns = _metrics.path_max_drawdowns(scenarios.returns)

    return RiskReport(
        artifact_id=scenarios.artifact_id,
        horizon=scenarios.request.horizon,
        n_paths=scenarios.request.n_paths,
        var_es=var_es_by_level,
        max_drawdown_median=float(np.median(drawdowns)),
    )
