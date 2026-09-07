"""ModelArtifact and its constituent value objects (architecture plan Section 8).

Everything here is a plain frozen dataclass over data already produced by
``xtra_takehome`` -- no statistical formula, threshold, or RNG rule is
reimplemented in this module. ``ModelArtifact`` exists to make the fitted
model a durable, versioned, semantically-identified object instead of a live
``GjrSkewTGenerator`` that only exists in one process's memory.

Two invariants this module is directly responsible for keeping true
(AGENTS.md 27 and 29):

* **Invariant 27.** ``fitted_residuals``/``fitted_variances`` are required
  fields, not optional cache data. ``GjrSkewTGenerator``'s default
  ``historical_mix`` initialisation samples from exactly these arrays
  (``rng.integers(0, residuals.size, n_paths)``), so an artifact missing them
  cannot reproduce the model it claims to represent. There is deliberately no
  "params-only" artifact constructor anywhere in this module.
* **Invariant 29.** ``artifact_id`` is a canonical hash of decoded semantic
  values (see ``identity.py``), verified on every load rather than trusted
  (``verify_identity``/``ArtifactIntegrityError``, Section 8.3's fail-closed
  design).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import numpy.typing as npt
import pandas as pd

from xtra_takehome.challenger import GjrSkewTParams

from . import identity as _identity


class ArtifactIntegrityError(RuntimeError):
    """Raised when a loaded artifact's recomputed identity does not match its stored id.

    Section 8.3: this is the fail-closed check that turns "artifacts are
    immutable" from a policy statement into something actually verified end
    to end. It fires on object mutation in storage, a truncated read, or a
    caller that assembled a ``ModelArtifact`` from mismatched pieces -- the
    reason does not matter; a mismatch is never trusted.
    """


class ArtifactValidationError(ValueError):
    """Raised when a ModelArtifact is constructed from structurally invalid fitted state.

    Distinct from :class:`ArtifactIntegrityError` (a content/identity
    mismatch on an otherwise well-formed object): this fires on malformed
    *shape* or *values* -- mismatched-length state arrays, a 2-D array, an
    empty array, a non-finite residual or variance, a non-positive
    variance, or an unsupported ``family``/``schema_version`` -- before any
    identity computation is even meaningful. AGENTS.md invariant 27's
    "``historical_mix`` samples from these arrays" is not just about their
    presence; a malformed array here would corrupt sampling or produce
    nonsensical scenarios silently rather than fail loudly at construction.
    """


#: The one family this repository submits (``challenger.py``'s
#: ``GjrSkewTGenerator``). A second family would need a second canonical
#: encoding, not a widened string -- see identity.py.
_SUPPORTED_FAMILIES: tuple[str, ...] = ("gjr-skewt",)


@dataclass(frozen=True)
class DatasetRef:
    """A price series' canonical identity, independent of storage format.

    ``dataset_id`` is computed once, over the *decoded* series (ticker,
    return definition, index as int64 epoch-nanoseconds, close as float64),
    by :func:`build_dataset_ref` below -- never recomputed from whatever CSV
    or Parquet bytes happen to be sitting on disk. See ``identity.py``'s
    ``compute_dataset_id`` and its docstring for exactly what is hashed.
    """

    ticker: str
    start: str
    end: str  # exclusive, matching xtra_takehome.config.Config.end
    dataset_id: str
    uri: str
    n_observations: int
    return_definition: str = "100 * log(P_t / P_{t-1})"


def build_dataset_ref(
    *,
    ticker: str,
    start: str,
    end: str,
    close: pd.Series,
    uri: str,
    return_definition: str = "100 * log(P_t / P_{t-1})",
) -> DatasetRef:
    """Compute a :class:`DatasetRef` from a fetched close-price series.

    ``close`` is expected to be exactly what ``xtra_takehome.data.fetch_close``
    returns: a ``pandas.Series`` indexed by date, values as float. The index
    is decoded to int64 epoch-nanoseconds and the values to float64 before
    hashing, so two calls with logically identical data produce the same
    ``dataset_id`` even if the series arrived via different transport formats
    upstream (CSV, Parquet, a fresh fetch) -- see
    ``tests/test_artifact_identity.py`` for the re-encoding proof.
    """
    index_ns = np.asarray(close.index.values, dtype="datetime64[ns]").astype(np.int64)
    close_values = np.asarray(close, dtype=np.float64)
    dataset_id = _identity.compute_dataset_id(
        ticker=ticker,
        return_definition=return_definition,
        index_ns=index_ns,
        close=close_values,
    )
    return DatasetRef(
        ticker=ticker,
        start=start,
        end=end,
        dataset_id=dataset_id,
        uri=uri,
        n_observations=int(close.size),
        return_definition=return_definition,
    )


@dataclass(frozen=True)
class StructuralDiagnostics:
    """Statistical facts about the fitted process, computed once at calibration.

    These are facts, not decisions -- see ``policies.py`` for what may be
    *done* with them. Nothing in this dataclass, or in the code that computes
    it, refers to what output a caller is or is not allowed to request.

    ``finite_second_moment``/``finite_third_moment`` are the direct
    tail-index criterion (``implied_return_tail_index > k`` for k = 2, 3).
    ``finite_fourth_moment`` additionally requires
    ``fourth_moment_coefficient < 1.0`` -- the classical GJR-GARCH
    finite-fourth-moment condition -- because the architecture plan's own
    worked example cites both facts together ("4 > 2.696, and E[A(z)^2] >=
    1"); for the fitted process today both criteria agree (both say False),
    and requiring both is the more conservative reading where they might not.
    """

    effective_persistence: float
    fourth_moment_coefficient: float
    implied_unconditional_variance: float
    implied_return_tail_index: float
    hill_tail_index: float
    finite_second_moment: bool
    finite_third_moment: bool
    finite_fourth_moment: bool


@dataclass(frozen=True)
class Provenance:
    """Everything needed to answer "where did this artifact come from" (Section 16.3).

    ``git_sha``/``dependency_lock_hash`` are ``None`` in Phase 1: there is no
    CI/build pipeline yet to populate them from, and inventing a value here
    would be worse than admitting it is not yet known. Phase 2's build
    pipeline fills them in; the field names are reserved now so the schema
    does not change shape later.
    """

    dataset_id: str
    dataset_uri: str
    calibration_window_start: str
    calibration_window_end: str  # exclusive, matches DatasetRef.end
    calibration_timestamp: str  # ISO 8601 UTC
    git_sha: str | None = None
    dependency_lock_hash: str | None = None


@dataclass(frozen=True)
class ModelArtifact:
    """A versioned, semantically-identified, fully reproducible fitted model.

    Field-by-field, why each one is semantic content and not incidental
    metadata:

    ``schema_version``
        The transport/domain schema this object was built against
        (``identity.ARTIFACT_SCHEMA`` today). Lets a future encoding change
        be detected explicitly rather than silently misread.
    ``model_version``
        The human-readable registry handle (``"{family}-{YYYYMMDD}-{n}"``
        in production; caller-supplied here). Distinct from ``artifact_id``:
        this is what a person names a version, the id is what a machine
        verifies it by.
    ``artifact_id``
        The canonical content identity (``identity.compute_artifact_id``).
        Recomputed and checked on every load (``verify_identity``) -- never
        trusted blindly.
    ``family``
        The model family. Pinned to the one family this repository submits;
        a second family would need a second canonical encoding, not a widened
        string.
    ``params``
        The existing ``GjrSkewTParams`` frozen dataclass, verbatim -- not
        re-encoded into a different representation.
    ``fitted_residuals`` / ``fitted_variances``
        REQUIRED (invariant 27). The fitted (residual, conditional variance)
        state pairs ``historical_mix`` samples from. Without these, an
        artifact changes the initialisation law it claims to reproduce.
    ``diagnostics``
        Structural facts computed once at calibration (Section 8.6).
    ``provenance``
        Where this artifact's inputs came from (Section 16.3).
    ``threshold_set_version`` / ``policy_set_version``
        Which validation tolerances and which governance policy set judged
        (or will judge) this artifact -- both versioned independently of the
        model itself, so either can be revised without a new fit.

    Mutation safety. ``dataclass(frozen=True)`` blocks *reassigning*
    ``artifact.fitted_residuals = ...``, but does nothing about mutating the
    array *in place* (``artifact.fitted_residuals[0] = 0.0`` would silently
    invalidate ``artifact_id`` without tripping any dataclass check).
    ``__post_init__`` therefore defensively copies both arrays and marks them
    read-only (``numpy``'s own ``WRITEABLE`` flag) -- an in-place write raises
    ``ValueError`` immediately rather than corrupting the artifact silently.

    Structural validation. ``__post_init__`` also rejects a structurally
    invalid fitted state before it is ever accepted as a real
    ``ModelArtifact`` (raising :class:`ArtifactValidationError`): unsupported
    ``family``/``schema_version``, a state array that is not 1-D, state
    arrays of different length, an empty state, a non-finite residual or
    variance, or a variance that is not strictly positive. This is a
    structural check, not a statistical one -- it does not constrain
    parameter *values* beyond what the quantitative core already guarantees.
    """

    schema_version: str
    model_version: str
    artifact_id: str
    family: Literal["gjr-skewt"]
    params: GjrSkewTParams
    fitted_residuals: npt.NDArray[np.float64]
    fitted_variances: npt.NDArray[np.float64]
    diagnostics: StructuralDiagnostics
    provenance: Provenance
    threshold_set_version: str
    policy_set_version: str

    def __post_init__(self) -> None:
        if self.family not in _SUPPORTED_FAMILIES:
            raise ArtifactValidationError(
                f"unsupported family {self.family!r}; expected one of {_SUPPORTED_FAMILIES}"
            )
        if self.schema_version != _identity.ARTIFACT_SCHEMA:
            raise ArtifactValidationError(
                f"unsupported schema_version {self.schema_version!r}; "
                f"expected {_identity.ARTIFACT_SCHEMA!r}"
            )

        residuals = np.asarray(self.fitted_residuals, dtype=np.float64)
        variances = np.asarray(self.fitted_variances, dtype=np.float64)

        if residuals.ndim != 1:
            raise ArtifactValidationError(
                f"fitted_residuals must be 1-D, got shape {residuals.shape}"
            )
        if variances.ndim != 1:
            raise ArtifactValidationError(
                f"fitted_variances must be 1-D, got shape {variances.shape}"
            )
        if residuals.shape != variances.shape:
            raise ArtifactValidationError(
                "fitted_residuals and fitted_variances must have the same shape, "
                f"got {residuals.shape} and {variances.shape}"
            )
        if residuals.size == 0:
            raise ArtifactValidationError("fitted state arrays must not be empty")
        if not np.all(np.isfinite(residuals)):
            raise ArtifactValidationError("fitted_residuals contains a non-finite value")
        if not np.all(np.isfinite(variances)):
            raise ArtifactValidationError("fitted_variances contains a non-finite value")
        if not np.all(variances > 0.0):
            raise ArtifactValidationError("fitted_variances must be strictly positive")

        residuals = np.array(residuals, copy=True)
        variances = np.array(variances, copy=True)
        residuals.setflags(write=False)
        variances.setflags(write=False)
        object.__setattr__(self, "fitted_residuals", residuals)
        object.__setattr__(self, "fitted_variances", variances)

    def recompute_artifact_id(self) -> str:
        """Recompute ``artifact_id`` from this object's current semantic content.

        Never reads ``self.artifact_id``; this is the "recompute from
        scratch" half of the fail-closed check in :meth:`verify_identity`.
        Covers ``diagnostics`` as well as ``params``/the state arrays --
        see ``identity.py``'s module docstring for exactly why (a stored
        ``StructuralDiagnostics`` that silently disagreed with its own
        artifact would let a policy decision diverge from what
        ``artifact_id`` claims to represent).
        """
        return _identity.compute_artifact_id(
            family=self.family,
            threshold_set_version=self.threshold_set_version,
            policy_set_version=self.policy_set_version,
            dataset_id=self.provenance.dataset_id,
            calibration_start=self.provenance.calibration_window_start,
            calibration_end=self.provenance.calibration_window_end,
            params=self.params,
            fitted_residuals=self.fitted_residuals,
            fitted_variances=self.fitted_variances,
            diagnostics=self.diagnostics,
        )

    def verify_identity(self) -> None:
        """Fail closed: raise if the stored ``artifact_id`` does not match recomputation.

        Called unconditionally by ``serialization.load_artifact`` after every
        read (Section 8.3). Never skipped, never made optional -- a caller
        that wants to construct an artifact without this check is
        constructing something that has not been verified to be what it
        claims, which is precisely the state this method exists to prevent
        from going unnoticed.
        """
        recomputed = self.recompute_artifact_id()
        if recomputed != self.artifact_id:
            raise ArtifactIntegrityError(
                f"artifact_id mismatch for model_version={self.model_version!r}: "
                f"stored {self.artifact_id!r}, recomputed {recomputed!r} from the "
                "artifact's own semantic content -- refusing to trust this artifact"
            )
