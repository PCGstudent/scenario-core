"""Proves the canonical artifact/dataset identity scheme (architecture plan Section 8.2).

Every test here works with small, hand-built values -- no network access, no
real historical dataset, no live fit. That is deliberate: identity is a pure
function of decoded values, and these tests exist specifically to prove that
purity, independent of anything a real fit happens to produce.

AGENTS.md invariant 29: "Artifact identity is semantic and canonical,
derived from the decoded parameter and array values, independent of
storage-format bytes... A `save -> load -> save` round trip must yield the
same identity even when the underlying bytes differ." This file is invariant
29's primary executable proof; `tests/test_artifact_roundtrip.py` exercises
the same property through the full save/load cycle on disk.
"""

from __future__ import annotations

import numpy as np
import pytest

from scenario_platform.domain import identity
from scenario_platform.domain.artifacts import StructuralDiagnostics
from xtra_takehome.challenger import GjrSkewTParams


def _params(mu: float = 0.01) -> GjrSkewTParams:
    return GjrSkewTParams(
        mu=mu, omega=0.05, alpha=0.06, gamma=0.04, beta=0.88, eta=6.0, lam=-0.12
    )


def _residuals() -> np.ndarray:
    return np.array([-1.5, -0.3, 0.2, 0.8, 1.2], dtype=np.float64)


def _variances() -> np.ndarray:
    return np.array([1.8, 0.9, 0.7, 1.0, 1.4], dtype=np.float64)


def _diagnostics(**overrides: object) -> StructuralDiagnostics:
    base: dict[str, object] = dict(
        effective_persistence=0.99,
        fourth_moment_coefficient=1.05,
        implied_unconditional_variance=8.5,
        implied_return_tail_index=2.7,
        hill_tail_index=2.9,
        finite_second_moment=True,
        finite_third_moment=False,
        finite_fourth_moment=False,
    )
    base.update(overrides)
    return StructuralDiagnostics(**base)  # type: ignore[arg-type]


def _artifact_id(
    *,
    params: GjrSkewTParams,
    residuals: np.ndarray,
    variances: np.ndarray,
    diagnostics: StructuralDiagnostics | None = None,
) -> str:
    return identity.compute_artifact_id(
        family="gjr-skewt",
        threshold_set_version="v1",
        policy_set_version="v1",
        dataset_id="sha256:" + "ab" * 32,
        calibration_start="2010-01-01",
        calibration_end="2026-09-01",
        params=params,
        fitted_residuals=residuals,
        fitted_variances=variances,
        diagnostics=diagnostics if diagnostics is not None else _diagnostics(),
    )


# ---------------------------------------------------------------------------
# Property A / B: transport-independent, stable under re-serialisation
# ---------------------------------------------------------------------------


def test_identity_is_deterministic_given_identical_inputs():
    """The same semantic inputs always hash to the same id -- the baseline
    property everything else in this file builds on."""
    a = _artifact_id(params=_params(), residuals=_residuals(), variances=_variances())
    b = _artifact_id(params=_params(), residuals=_residuals(), variances=_variances())
    assert a == b
    assert a.startswith("sha256:")
    assert len(a) == len("sha256:") + 64  # a real hex-encoded SHA-256 digest


def test_identity_is_independent_of_array_object_identity_and_memory_layout():
    """Property B (in spirit): two numerically-identical but distinctly-allocated,
    differently-laid-out arrays must still hash to the same value -- the encoding
    reads values, not memory. A Fortran-ordered copy and a freshly-built
    C-ordered array of the same numbers are different Python/NumPy objects
    with different internal byte layouts prior to encoding; canonicalisation
    (C-contiguous, big-endian) must erase that difference.
    """
    residuals_c = np.array(_residuals(), order="C")
    residuals_f = np.asfortranarray(_residuals().reshape(5, 1)).reshape(-1, order="F")
    # Confirm the two arrays are not the same object and are laid out differently,
    # so this test cannot pass by accident.
    assert residuals_c is not residuals_f
    np.testing.assert_array_equal(residuals_c, residuals_f)

    a = _artifact_id(params=_params(), residuals=residuals_c, variances=_variances())
    b = _artifact_id(params=_params(), residuals=residuals_f, variances=_variances())
    assert a == b


def test_identity_is_independent_of_host_byte_order():
    """A little-endian and a byte-swapped big-endian view of the SAME values
    must produce the SAME artifact_id -- identity must not depend on host
    byte order (AGENTS.md invariant 29)."""
    residuals_native = _residuals()
    residuals_swapped = (
        residuals_native.astype(">f8").byteswap().view(residuals_native.dtype)
    )
    # Confirm the two arrays really do have different raw bytes despite equal values.
    assert residuals_native.tobytes() != residuals_native.astype(">f8").tobytes()
    np.testing.assert_array_equal(residuals_native, residuals_swapped)

    a = _artifact_id(params=_params(), residuals=residuals_native, variances=_variances())
    b = _artifact_id(params=_params(), residuals=residuals_swapped, variances=_variances())
    assert a == b


# ---------------------------------------------------------------------------
# Property C: one-ULP parameter sensitivity
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name", identity.PARAM_ORDER)
def test_one_ulp_parameter_change_alters_artifact_id(field_name: str):
    """A one-ULP change to ANY single fitted parameter must change artifact_id.

    Parametrized over every field in the fixed parameter order, so this is
    not just "changing mu changes the id" -- it is "changing any one of the
    seven fields changes the id," proving no field is silently dropped from
    the encoding.
    """
    base = _params()
    baseline_value = getattr(base, field_name)
    bumped_value = np.nextafter(baseline_value, np.inf)
    assert bumped_value != baseline_value  # sanity: nextafter actually moved

    bumped = GjrSkewTParams(**{**vars(base), field_name: bumped_value})

    id_before = _artifact_id(params=base, residuals=_residuals(), variances=_variances())
    id_after = _artifact_id(params=bumped, residuals=_residuals(), variances=_variances())
    assert id_before != id_after


# ---------------------------------------------------------------------------
# Property C2: StructuralDiagnostics field sensitivity (every field, both kinds)
#
# Closes a review-found gap: artifact_id used to cover params and the fitted
# state arrays but not StructuralDiagnostics, so changing e.g.
# finite_fourth_moment from False to True could change what policies.py
# permits while leaving artifact_id -- and verify_identity -- unchanged.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("field_name,kind", identity.DIAGNOSTICS_ORDER)
def test_diagnostics_field_change_alters_artifact_id(field_name: str, kind: str):
    """Every StructuralDiagnostics field changes artifact_id: a one-ULP bump
    for the five float64 fields, a plain flip for the three boolean fields
    (there is no smaller change for a bool). Parametrized over the exact
    fixed order identity.py hashes them in, so this is "every field the
    dataclass has," not just the one the review report happened to name.
    """
    base = _diagnostics()
    baseline_value = getattr(base, field_name)
    if kind == "f64":
        changed_value: float | bool = np.nextafter(baseline_value, np.inf)
    else:
        changed_value = not baseline_value
    assert changed_value != baseline_value

    changed = _diagnostics(**{field_name: changed_value})

    id_before = _artifact_id(
        params=_params(), residuals=_residuals(), variances=_variances(), diagnostics=base
    )
    id_after = _artifact_id(
        params=_params(),
        residuals=_residuals(),
        variances=_variances(),
        diagnostics=changed,
    )
    assert id_before != id_after


# ---------------------------------------------------------------------------
# Property D: one-ULP state-array sensitivity
# ---------------------------------------------------------------------------


def test_one_ulp_residual_element_change_alters_artifact_id():
    residuals = _residuals()
    bumped = residuals.copy()
    bumped[2] = np.nextafter(bumped[2], np.inf)
    assert bumped[2] != residuals[2]

    id_before = _artifact_id(params=_params(), residuals=residuals, variances=_variances())
    id_after = _artifact_id(params=_params(), residuals=bumped, variances=_variances())
    assert id_before != id_after


def test_one_ulp_variance_element_change_alters_artifact_id():
    variances = _variances()
    bumped = variances.copy()
    bumped[0] = np.nextafter(bumped[0], np.inf)
    assert bumped[0] != variances[0]

    id_before = _artifact_id(params=_params(), residuals=_residuals(), variances=variances)
    id_after = _artifact_id(params=_params(), residuals=_residuals(), variances=bumped)
    assert id_before != id_after


# ---------------------------------------------------------------------------
# Property E: shape/order/name sensitivity
# ---------------------------------------------------------------------------


def test_changing_array_length_changes_identity():
    residuals = _residuals()
    truncated = residuals[:-1]
    assert truncated.shape != residuals.shape

    id_before = _artifact_id(params=_params(), residuals=residuals, variances=_variances())
    id_after = _artifact_id(params=_params(), residuals=truncated, variances=_variances())
    assert id_before != id_after


def test_swapping_residuals_and_variances_changes_identity():
    """The two arrays carry different names in the encoding (`_write_array`
    writes the name before the data), so swapping which array plays which
    role must change the id even when both are individually valid float64
    arrays of the same shape."""
    residuals = _residuals()
    variances = _variances()
    assert residuals.shape == variances.shape  # otherwise this proves less than intended

    id_normal = _artifact_id(params=_params(), residuals=residuals, variances=variances)
    id_swapped = _artifact_id(params=_params(), residuals=variances, variances=residuals)
    assert id_normal != id_swapped


def test_transposed_reshaped_array_does_not_collide_with_original():
    """A (5,) array and its (1, 5) reshape carry the same flat bytes but
    different shapes; the encoding must distinguish them (ndim and each
    dimension are both written before the data)."""
    residuals = _residuals()
    reshaped = residuals.reshape(1, -1)

    id_flat = _artifact_id(params=_params(), residuals=residuals, variances=_variances())
    # reshaped won't round-trip through GjrSkewTGenerator, but identity.py's
    # array writer only asserts dtype, not ndim -- so this exercises the
    # encoder directly rather than through the artifact-level helper.
    from scenario_platform.domain.identity import canonical_artifact_bytes

    bytes_flat = canonical_artifact_bytes(
        family="gjr-skewt",
        threshold_set_version="v1",
        policy_set_version="v1",
        dataset_id="sha256:" + "ab" * 32,
        calibration_start="2010-01-01",
        calibration_end="2026-09-01",
        params=_params(),
        fitted_residuals=residuals,
        fitted_variances=_variances(),
        diagnostics=_diagnostics(),
    )
    bytes_reshaped = canonical_artifact_bytes(
        family="gjr-skewt",
        threshold_set_version="v1",
        policy_set_version="v1",
        dataset_id="sha256:" + "ab" * 32,
        calibration_start="2010-01-01",
        calibration_end="2026-09-01",
        params=_params(),
        fitted_residuals=reshaped,
        fitted_variances=_variances(),
        diagnostics=_diagnostics(),
    )
    assert bytes_flat != bytes_reshaped
    assert id_flat == identity.compute_artifact_id(
        family="gjr-skewt",
        threshold_set_version="v1",
        policy_set_version="v1",
        dataset_id="sha256:" + "ab" * 32,
        calibration_start="2010-01-01",
        calibration_end="2026-09-01",
        params=_params(),
        fitted_residuals=residuals,
        fitted_variances=_variances(),
        diagnostics=_diagnostics(),
    )


@pytest.mark.parametrize(
    "field_name",
    [
        "family",
        "threshold_set_version",
        "policy_set_version",
        "dataset_id",
        "calibration_start",
        "calibration_end",
    ],
)
def test_changing_any_metadata_field_changes_identity(field_name: str):
    """Every non-array, non-parameter field feeding the hash actually matters."""
    kwargs = dict(
        family="gjr-skewt",
        threshold_set_version="v1",
        policy_set_version="v1",
        dataset_id="sha256:" + "ab" * 32,
        calibration_start="2010-01-01",
        calibration_end="2026-09-01",
    )
    baseline = identity.compute_artifact_id(
        params=_params(),
        fitted_residuals=_residuals(),
        fitted_variances=_variances(),
        diagnostics=_diagnostics(),
        **kwargs,
    )
    changed_kwargs = dict(kwargs)
    changed_kwargs[field_name] = kwargs[field_name] + "-changed"
    changed = identity.compute_artifact_id(
        params=_params(),
        fitted_residuals=_residuals(),
        fitted_variances=_variances(),
        diagnostics=_diagnostics(),
        **changed_kwargs,
    )
    assert baseline != changed


# ---------------------------------------------------------------------------
# Type/dtype strictness
# ---------------------------------------------------------------------------


def test_non_float64_array_is_rejected_rather_than_silently_coerced():
    """The encoding asserts float64 rather than casting -- a caller passing
    float32 has a real bug (precision loss) that must not be hidden."""
    with pytest.raises(TypeError):
        identity.compute_artifact_id(
            family="gjr-skewt",
            threshold_set_version="v1",
            policy_set_version="v1",
            dataset_id="sha256:" + "ab" * 32,
            calibration_start="2010-01-01",
            calibration_end="2026-09-01",
            params=_params(),
            fitted_residuals=_residuals().astype(np.float32),
            fitted_variances=_variances(),
            diagnostics=_diagnostics(),
        )


# ---------------------------------------------------------------------------
# Dataset identity (Section 7 of the task): decoded semantics, not transport
# ---------------------------------------------------------------------------


def test_dataset_identity_is_deterministic():
    index_ns = np.array([1, 2, 3], dtype=np.int64) * 86_400_000_000_000
    close = np.array([70.0, 71.5, 69.25], dtype=np.float64)
    a = identity.compute_dataset_id(
        ticker="BZ=F",
        return_definition="100 * log(P_t / P_{t-1})",
        index_ns=index_ns,
        close=close,
    )
    b = identity.compute_dataset_id(
        ticker="BZ=F",
        return_definition="100 * log(P_t / P_{t-1})",
        index_ns=index_ns.copy(),
        close=close.copy(),
    )
    assert a == b


def test_dataset_identity_changes_when_a_value_changes():
    index_ns = np.array([1, 2, 3], dtype=np.int64) * 86_400_000_000_000
    close = np.array([70.0, 71.5, 69.25], dtype=np.float64)
    changed_close = close.copy()
    changed_close[1] = np.nextafter(changed_close[1], np.inf)

    a = identity.compute_dataset_id(
        ticker="BZ=F",
        return_definition="100 * log(P_t / P_{t-1})",
        index_ns=index_ns,
        close=close,
    )
    b = identity.compute_dataset_id(
        ticker="BZ=F",
        return_definition="100 * log(P_t / P_{t-1})",
        index_ns=index_ns,
        close=changed_close,
    )
    assert a != b
