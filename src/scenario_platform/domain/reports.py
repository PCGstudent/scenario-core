"""Frozen result schemas returned by the Phase-1 service functions.

``ValidationReport`` wraps the existing repository's own gate/diagnostic
dataclasses (``xtra_takehome.validation``) verbatim -- it does not
re-represent a ``Gate`` or a ``MatchedSampleReference`` in a new shape. This
is deliberate: the whole point of ``services.validate`` is that its output
*is* the repository's own validation, not a summary of it.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from xtra_takehome.validation import (
    Diagnostic,
    ExtremeRegionCheck,
    Gate,
    LeaveOutRow,
    MatchedSampleReference,
)
from xtra_takehome.windows import WindowStats

from .requests import ScenarioRequest


def _frozen_float64(array: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    """Defensively copy and write-protect an array meant to be immutable content.

    Same mutation-safety reasoning as ``ModelArtifact`` (artifacts.py): a
    frozen dataclass only stops *reassignment*, not in-place mutation of a
    mutable field's contents.
    """
    out = np.array(array, dtype=np.float64, copy=True)
    out.setflags(write=False)
    return out


@dataclass(frozen=True)
class ScenarioSet:
    """A generated set of scenario paths plus everything needed to reproduce it.

    ``request`` is the full, resolved request that produced ``returns`` --
    kept whole (rather than flattened into loose fields) so provenance is
    exact and no field can be forgotten when this object is logged or
    serialised later.
    """

    artifact_id: str
    request: ScenarioRequest
    returns: npt.NDArray[np.float64]
    variances: npt.NDArray[np.float64] | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "returns", _frozen_float64(self.returns))
        if self.variances is not None:
            object.__setattr__(self, "variances", _frozen_float64(self.variances))

    @property
    def conditional_volatility(self) -> npt.NDArray[np.float64] | None:
        """Conditional volatility paths, percent per day, or None if not requested."""
        if self.variances is None:
            return None
        return np.sqrt(self.variances).astype(np.float64)


@dataclass(frozen=True)
class ValidationReport:
    """The repository's own three-family validation, run against one artifact.

    Every field is a direct pass-through of what
    ``xtra_takehome.validation``/``xtra_takehome.windows`` already compute;
    see ``services.validate`` for exactly which functions produce which
    field, and Section 7.2 of the architecture plan for why this cannot be
    reduced to ``validate(real, simulated, config)`` without losing
    ``references``/``acf_floor_*`` (they require re-simulating from the
    artifact, not from one already-generated array).
    """

    artifact_id: str
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
    leave_out: list[LeaveOutRow]

    @property
    def pooled_passed(self) -> int:
        return sum(g.passed for g in self.pooled_gates)

    @property
    def pooled_total(self) -> int:
        return len(self.pooled_gates)

    @property
    def matched_passed(self) -> int:
        return sum(g.passed for g in self.matched_gates)

    @property
    def matched_total(self) -> int:
        return len(self.matched_gates)


@dataclass(frozen=True)
class RiskReport:
    """A pooled risk summary over a generated :class:`ScenarioSet`.

    ``var_es`` uses the existing positive-loss convention verbatim
    (``xtra_takehome.metrics.var_es``, loss = -return): both values in each
    ``(VaR, ES)`` pair are positive loss magnitudes, and ``ES >= VaR`` always
    holds by construction (the repository's own invariant, not re-derived
    here).
    """

    artifact_id: str
    horizon: int
    n_paths: int
    var_es: dict[float, tuple[float, float]]  # level -> (VaR, ES), both positive losses
    max_drawdown_median: float
