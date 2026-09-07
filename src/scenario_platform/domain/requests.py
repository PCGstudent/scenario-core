"""Frozen request/config schemas for the Phase-1 service functions.

These are domain objects, not control-plane objects: a seed is never
server-generated or optional here, and there is no idempotency concept.
Both belong to a future control plane's admission logic (architecture plan
Section 6.1), which sits in front of these services and resolves such things
*before* constructing a request. ``ScenarioRequest.seed`` is therefore a
required field with no default -- a caller (the control plane, a test, or
``scripts/build_artifact.py``) must always name it explicitly, exactly as
``AGENTS.md`` invariant 8 already requires of the quantitative core.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from .artifacts import DatasetRef

#: The three initial-state semantics already present in
#: ``GjrSkewTGenerator.simulate`` (``challenger.py``). Preserved verbatim,
#: not collapsed: each answers a different question (Section 2.3 of the
#: architecture plan) and the quantitative core already tests all three.
InitialState = Literal["historical_mix", "latest"] | tuple[float, float]

_KNOWN_INITIAL_STATE_LITERALS = ("historical_mix", "latest")


@dataclass(frozen=True)
class FitConfig:
    """What to fit, against which pre-identified dataset.

    ``dataset`` is a fully-resolved :class:`~scenario_platform.domain.artifacts.DatasetRef`
    (built via ``artifacts.build_dataset_ref`` from an already-fetched close
    price series), not a ticker/window pair that :func:`services.fit` would
    resolve itself. This mirrors the architecture plan's calibration
    lifecycle (Section 10), which snapshots and identifies a dataset as a
    step distinct from fitting -- ``fit`` never fetches data or computes
    dataset identity on its own.
    """

    dataset: DatasetRef
    model_version: str
    family: Literal["gjr-skewt"] = "gjr-skewt"
    threshold_set_version: str = "v1"
    policy_set_version: str = "v1"


@dataclass(frozen=True)
class ScenarioRequest:
    """A fully-specified request to simulate scenarios from a fitted artifact.

    ``rng_scheme`` carries both values the architecture plan's dataclass
    lists (Section 7.1) even though only ``"single"`` has implemented
    behaviour today: ``"sharded-v1"`` is a reserved, recognised-but-not-yet-
    implemented value (Section 21.4's scaling trap -- sharding changes the
    RNG stream and must always be an explicit, versioned opt-in, never a
    silent default). ``services.simulate`` raises ``NotImplementedError`` if
    it is ever requested, rather than silently falling back to ``"single"``.
    """

    model_version: str
    horizon: int
    n_paths: int
    seed: int  # never optional at this layer -- AGENTS.md invariant 8
    initial_state: InitialState = "historical_mix"
    rng_scheme: Literal["single", "sharded-v1"] = "single"
    return_variance: bool = False

    def __post_init__(self) -> None:
        if self.horizon <= 0:
            raise ValueError("horizon must be positive")
        if self.n_paths <= 0:
            raise ValueError("n_paths must be positive")
        if isinstance(self.initial_state, str):
            if self.initial_state not in _KNOWN_INITIAL_STATE_LITERALS:
                raise ValueError(
                    f"unknown initial_state {self.initial_state!r}; expected "
                    f"one of {_KNOWN_INITIAL_STATE_LITERALS} or an explicit "
                    "(residual, variance) tuple"
                )
        elif len(self.initial_state) != 2:
            raise ValueError(
                "an explicit initial_state must be a (residual, variance) pair"
            )


@dataclass(frozen=True)
class ValidationConfig:
    """The canonical validation configuration (architecture plan Section 7.2).

    Defaults match ``xtra_takehome.config.Config`` exactly -- the tolerances
    the validation suite's gates were derived for -- plus the extra budget
    knobs ``matched_sample_reference``/``acf_monte_carlo_floor`` need, so the
    simulation cost of a cheap CI lane can be tuned without changing the
    *shape* of what is checked (Section 7.2's central argument for why
    ``validate`` takes the artifact rather than a pre-simulated array).
    """

    horizon: int = 252
    n_paths: int = 1000
    seed: int = 42
    max_acf_lag: int = 20
    records_per_seed: int = 100
    reference_seeds: tuple[int, ...] = (701, 702, 703)
    acf_floor_seeds: tuple[int, ...] = (901, 902, 903, 904)
    leave_out_drops: tuple[int, ...] = (0, 1)


@dataclass(frozen=True)
class RiskConfig:
    """Configuration for a pooled risk report over a generated scenario set.

    ``levels`` are the VaR/ES confidence levels to report -- 95%/99% by
    default, matching the validated, unrestricted region of
    ``policies.py``'s tail-metric policy. Requesting a level policy
    classifies as restricted is a ``services.risk`` caller's decision to
    make explicitly (via ``governance``), not something this config silently
    permits by including an arbitrary level here.
    """

    levels: tuple[float, ...] = (0.95, 0.99)
