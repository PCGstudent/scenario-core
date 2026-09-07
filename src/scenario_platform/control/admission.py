"""Admission control and the approval predicate (architecture plan Section
6.1 steps 2e-2g, Section 8.4).

**Admission limits are DEV placeholder values, not numbers the architecture
plan pins.** Section 20.1/24 says only "``MAX_PATH_YEARS`` low, to bound
spend" for DEV and defers the exact figure to deployment-time measurement
(Section 26: "Re-derive ``MAX_PATH_YEARS`` and task sizes from Phase 3b
measurements"). ``300_000`` path-years is chosen as the smallest round
number comfortably above the plan's own canonical acceptance job (Section
24 Phase 3b acceptance criterion 1: "a 1,000-path job" at the canonical
252-day horizon = 252,000 path-years) -- rejecting that job with a tighter
"conservative" limit would fail the platform's own first vertical-slice
proof. At the plan's ~1.39 ms/path-year figure, 300,000 path-years bounds
worst-case compute at ~417s, comfortably inside
``modules/job_orchestrator``'s default 600s static task timeout. Both
constants are read from the environment so a later, evidence-based value
can be set without a code change.
"""

from __future__ import annotations

import os

from scenario_platform.domain.policies import (
    GovernanceCap,
    PolicyRejection,
    check_metric_request,
)

from ..adapters import job_store
from .errors import HandlerError
from .schemas import ScenarioJobIn

#: The one model family this platform serves (``domain.requests.FitConfig``'s
#: own default, ``domain.artifacts``'s one supported family) -- never taken
#: from caller input.
FAMILY = "gjr-skewt"

MAX_PATH_YEARS = int(os.environ.get("MAX_PATH_YEARS", "300000"))
MAX_HORIZON = int(os.environ.get("MAX_HORIZON", "2520"))
#: Section 6.3: "attempt < MAX_ATTEMPTS (2)" -- the one number the plan does
#: pin explicitly, so it is a real constant, not an environment default.
MAX_ATTEMPTS = 2


def check_admission(request: ScenarioJobIn) -> None:
    """Section 6.1 step 2e. ``n_paths``/``horizon`` positivity is already
    enforced by ``ScenarioJobIn``'s pydantic field constraints; this only
    checks the two limits that need a runtime-configured threshold."""
    if request.horizon > MAX_HORIZON:
        raise HandlerError(
            422,
            "AdmissionRejected",
            f"horizon {request.horizon} exceeds MAX_HORIZON={MAX_HORIZON}",
        )
    path_years = request.n_paths * request.horizon
    if path_years > MAX_PATH_YEARS:
        raise HandlerError(
            422,
            "AdmissionRejected",
            f"n_paths * horizon = {path_years} exceeds MAX_PATH_YEARS={MAX_PATH_YEARS}",
        )


def check_policy(request: ScenarioJobIn) -> None:
    """Section 6.1 step 2g: every requested metric/level against the
    artifact-independent tail-metric policy (Section 8.5) -- the exact same
    check the worker's own request transport already performs for
    ``risk_levels``, reused rather than re-derived."""
    governance = (
        GovernanceCap(cap=request.governance.cap, approver=request.governance.approver)
        if request.governance is not None
        else None
    )
    for level in request.metrics:
        try:
            check_metric_request("tail expectation", level=level, governance=governance)
        except PolicyRejection as exc:
            raise HandlerError(422, "PolicyRejection", str(exc)) from exc


def resolve_and_authorize(model_version: str) -> tuple[str, str]:
    """Section 6.1 step 2f + Section 8.4's derived approval predicate.

    Returns ``(resolved_model_version, resolved_artifact_id)``. Never
    resolves ``"current"`` a second time for an idempotent replay -- callers
    with a matched idempotency record must use the frozen values from that
    record instead of calling this function again (Section 6.2's whole
    point: "the retry reproduces the original decision rather than a new
    one").
    """
    if model_version == "current":
        pointer = job_store.get_pointer(FAMILY)
        if pointer is None:
            raise HandlerError(
                422, "NoCurrentPointer", f"no POINTER#{FAMILY}/CURRENT exists yet"
            )
        resolved_version = str(pointer["model_version"])
    else:
        resolved_version = model_version
        pointer = None

    candidate = job_store.get_candidate(FAMILY, resolved_version)
    approval = job_store.get_approval(FAMILY, resolved_version)
    if (
        candidate is None
        or approval is None
        or approval.get("artifact_id") != candidate.get("artifact_id")
    ):
        raise HandlerError(
            422,
            "NotApproved",
            f"model_version {resolved_version!r} has no matching CANDIDATE#/APPROVAL# "
            "pair with agreeing artifact_id (Section 8.4)",
        )
    if pointer is not None and pointer.get("artifact_id") != candidate["artifact_id"]:
        raise HandlerError(
            422,
            "PointerInconsistent",
            f"POINTER#{FAMILY}/CURRENT names an artifact_id that disagrees with "
            f"CANDIDATE#{FAMILY}#{resolved_version}",
        )
    return resolved_version, str(candidate["artifact_id"])
