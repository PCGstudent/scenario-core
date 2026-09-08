"""Wire schemas for the scenario-jobs API (architecture plan Section 6.1).

``ScenarioJobIn`` is deliberately NOT ``scenario_platform.domain.requests.
ScenarioRequest``: the domain type requires ``seed`` (a fitted invariant,
AGENTS.md invariant 8) and knows nothing about idempotency, governance
caps or a "current" pointer literal -- all control-plane admission concepts
that must be resolved *before* a ``ScenarioRequest`` can even be
constructed (Section 7.4, "shared contract without shared runtime"). This
module owns the wire shape; ``admission.py`` owns turning a validated
``ScenarioJobIn`` into a resolved job.

Field ``metrics`` is this module's name for what the worker's own request
transport (``worker/__main__.py``) calls ``risk_levels`` -- a list of
confidence levels in (0, 1) for VaR/ES, e.g. ``[0.95, 0.99]``. Section 6.1's
wire sketch names the field ``metrics`` at the API layer; nothing in the
domain layer supports selecting *named* metrics independently of a
confidence level, so this module carries the same numeric-level semantics
under the API's own field name and maps it to ``risk_levels`` at the
job-document boundary (``admission.py``) and, unchanged, into the worker's
existing request transport.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

InitialStateIn = Literal["historical_mix", "latest"] | tuple[float, float]


class GovernanceIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    cap: str
    approver: str


class ScenarioJobIn(BaseModel):
    """The exact field set Section 6.2's ``client_request_hash`` is computed
    over, plus nothing else -- ``extra="forbid"`` so an unrecognised field is
    a 422, not a silently ignored typo (the same discipline the worker's own
    ``_KNOWN_REQUEST_FIELDS`` enforces)."""

    model_config = ConfigDict(extra="forbid")

    model_version: str = "current"
    horizon: int = Field(gt=0)
    n_paths: int = Field(gt=0)
    seed: int | None = Field(default=None, ge=0)
    initial_state: InitialStateIn = "historical_mix"
    metrics: list[float] = Field(default_factory=lambda: [0.95, 0.99])
    rng_scheme: Literal["single", "sharded-v1"] = "single"
    include_variance: bool = False
    governance: GovernanceIn | None = None

    @model_validator(mode="after")
    def _validate_metrics(self) -> ScenarioJobIn:
        if not self.metrics:
            raise ValueError("metrics must be a non-empty list of confidence levels")
        for level in self.metrics:
            if not (0.0 < level < 1.0):
                raise ValueError(
                    f"metrics entries must be strictly between 0 and 1, got {level}"
                )
        return self


class ScenarioJobOut(BaseModel):
    """``202 Accepted`` body (Section 6.1 step 2k) and the ``200`` replay body
    (Section 6.2's table)."""

    job_id: str
    status: str
    poll: str


class ErrorOut(BaseModel):
    error: str
    message: str
