"""Proves ModelArtifact.__post_init__ rejects structurally invalid fitted state.

Phase-1 review finding: nothing previously stopped a caller (or a corrupted
load path) from constructing a ``ModelArtifact`` whose ``fitted_residuals``/
``fitted_variances`` were the wrong shape, empty, non-finite, or carried a
non-positive variance -- ``historical_mix`` (``challenger.py``) would either
crash confusingly deep inside simulation or, worse, silently sample garbage.
``__post_init__`` now checks this structurally (not statistically -- it does
not constrain any parameter *value* the quantitative core does not already
guarantee) before ever accepting the object as a real artifact.

These tests bypass :func:`~scenario_platform.domain.services.fit` entirely
and build ``ModelArtifact`` directly with a placeholder ``artifact_id``: the
structural checks under test run in ``__post_init__``, before
``verify_identity()`` is ever called, so a mismatched id is irrelevant to
what is being proven here.
"""

from __future__ import annotations

import numpy as np
import pytest

from scenario_platform.domain import identity
from scenario_platform.domain.artifacts import (
    ArtifactValidationError,
    ModelArtifact,
    Provenance,
    StructuralDiagnostics,
)
from xtra_takehome.challenger import GjrSkewTParams


def _params() -> GjrSkewTParams:
    return GjrSkewTParams(
        mu=0.01, omega=0.05, alpha=0.06, gamma=0.04, beta=0.88, eta=6.0, lam=-0.12
    )


def _diagnostics() -> StructuralDiagnostics:
    return StructuralDiagnostics(
        effective_persistence=0.99,
        fourth_moment_coefficient=1.05,
        implied_unconditional_variance=8.5,
        implied_return_tail_index=2.7,
        hill_tail_index=2.9,
        finite_second_moment=True,
        finite_third_moment=False,
        finite_fourth_moment=False,
    )


def _provenance() -> Provenance:
    return Provenance(
        dataset_id="sha256:" + "ab" * 32,
        dataset_uri="memory://test",
        calibration_window_start="2010-01-01",
        calibration_window_end="2026-09-01",
        calibration_timestamp="2026-09-07T00:00:00+00:00",
    )


def _build(**overrides: object) -> ModelArtifact:
    kwargs: dict[str, object] = dict(
        schema_version=identity.ARTIFACT_SCHEMA,
        model_version="test",
        # A placeholder, deliberately not recomputed: these tests exercise
        # __post_init__'s structural checks, which run before
        # verify_identity() is ever called, so a "wrong" id is irrelevant.
        artifact_id="sha256:" + "0" * 64,
        family="gjr-skewt",
        params=_params(),
        fitted_residuals=np.array([-1.5, -0.3, 0.2, 0.8, 1.2], dtype=np.float64),
        fitted_variances=np.array([1.8, 0.9, 0.7, 1.0, 1.4], dtype=np.float64),
        diagnostics=_diagnostics(),
        provenance=_provenance(),
        threshold_set_version="v1",
        policy_set_version="v1",
    )
    kwargs.update(overrides)
    return ModelArtifact(**kwargs)  # type: ignore[arg-type]


def test_valid_artifact_construction_succeeds():
    """Sanity: the helper's defaults really do build a valid artifact, so
    every failure below is attributable to the specific override made, not
    to a helper that is broken in general."""
    artifact = _build()
    assert artifact.fitted_residuals.shape == (5,)
    assert artifact.fitted_variances.shape == (5,)


def test_rejects_mismatched_state_array_lengths():
    with pytest.raises(ArtifactValidationError, match="same shape"):
        _build(fitted_variances=np.array([1.0, 2.0, 3.0], dtype=np.float64))


def test_rejects_2d_residuals_array():
    with pytest.raises(ArtifactValidationError, match="1-D"):
        _build(fitted_residuals=np.zeros((5, 1), dtype=np.float64))


def test_rejects_2d_variances_array():
    with pytest.raises(ArtifactValidationError, match="1-D"):
        _build(fitted_variances=np.ones((5, 1), dtype=np.float64))


def test_rejects_empty_state_arrays():
    with pytest.raises(ArtifactValidationError, match="empty"):
        _build(
            fitted_residuals=np.array([], dtype=np.float64),
            fitted_variances=np.array([], dtype=np.float64),
        )


def test_rejects_nan_residual():
    residuals = np.array([-1.5, np.nan, 0.2, 0.8, 1.2], dtype=np.float64)
    with pytest.raises(ArtifactValidationError, match="non-finite"):
        _build(fitted_residuals=residuals)


def test_rejects_infinite_variance():
    variances = np.array([1.8, np.inf, 0.7, 1.0, 1.4], dtype=np.float64)
    with pytest.raises(ArtifactValidationError, match="non-finite"):
        _build(fitted_variances=variances)


def test_rejects_zero_variance():
    variances = np.array([1.8, 0.0, 0.7, 1.0, 1.4], dtype=np.float64)
    with pytest.raises(ArtifactValidationError, match="strictly positive"):
        _build(fitted_variances=variances)


def test_rejects_negative_variance():
    variances = np.array([1.8, -0.5, 0.7, 1.0, 1.4], dtype=np.float64)
    with pytest.raises(ArtifactValidationError, match="strictly positive"):
        _build(fitted_variances=variances)


def test_rejects_unsupported_schema_version():
    with pytest.raises(ArtifactValidationError, match="schema_version"):
        _build(schema_version="scenario-core/artifact/v0")


def test_rejects_unsupported_family():
    with pytest.raises(ArtifactValidationError, match="family"):
        _build(family="not-a-real-family")
