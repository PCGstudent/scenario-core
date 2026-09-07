"""Governance policy over statistical facts (architecture plan Section 8.5).

The central correction this module makes over an earlier, rejected design:
a single ``far_tail_usable: bool`` flag would have conflated a statistical
fact with a business decision, and read naively would have suppressed
metrics that are perfectly well-defined. The fitted process has an implied
return tail index around 2.696, so ``E|r| < infinity`` (order 1 < 2.696):
**VaR and ES are finite, validated, and not restricted by anything in this
module.** What actually lacks a finite population value is the *third* and
*fourth* moments (skewness and kurtosis), which is a narrower and different
fact than "the tail is unusable."

This module therefore does two separate things, kept in two separate
functions so they cannot be conflated by a caller either:

``classify_tail_request``
    Classifies a *named metric or confidence level* into
    unrestricted / disclosure-required / restricted, based on the validated
    region (252-day horizon, up to the 99% level) versus the unvalidated
    extrapolation region beyond it. This does **not** depend on any
    particular artifact's diagnostics -- the boundary is a property of the
    validation methodology (Section 8.5's ``tail_metric_policy``), not of
    which model happened to be fitted.

``check_pooled_moment_point_estimate``
    Checks whether a *pooled moment* (skewness, excess kurtosis) may be
    reported as an ordinary finite-population point estimate, which
    genuinely does depend on the artifact's own
    ``StructuralDiagnostics.finite_third_moment`` /
    ``finite_fourth_moment`` -- a fact this function reads and never
    recomputes or overrides.

Policies consume facts. They never rewrite them: nowhere in this module is
a ``StructuralDiagnostics`` field assigned a new value.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Deferred exactly like domain/identity.py's own identical guard, and for
    # the same reason: `.artifacts` pulls in numpy/pandas/xtra_takehome.challenger
    # transitively (DatasetRef/ModelArtifact's array fields), and nothing at
    # runtime in this module needs the StructuralDiagnostics *class* -- only
    # its two attributes, read by ordinary attribute access below. A real
    # top-level import here would make this module (and everything that
    # imports it -- scenario_platform.control.admission, in particular)
    # transitively require the scientific stack just to import, which is
    # exactly the control-plane/data-plane boundary this codebase's own
    # `tests/test_control_plane_purity.py` exists to guard. `from __future__
    # import annotations` above keeps the type annotation below valid as a
    # lazy string even though the name is undefined at runtime.
    from .artifacts import StructuralDiagnostics

POLICY_SET_VERSION = "v1"

#: The validated region's upper confidence-level boundary (architecture plan
#: Section 8.5: "VaR 95%, VaR 99%, ES 95%, ES 99% ... horizon: the validated
#: horizon only"). Both endpoints of the plan's three-way split are named
#: constants rather than inline numbers, so the boundary is documented once.
_VALIDATED_LEVEL_CEILING = 0.99
_DISCLOSURE_LEVEL_CEILING = 0.995


class MetricRestriction(StrEnum):
    """Where a requested metric or confidence level sits under the policy."""

    UNRESTRICTED = "unrestricted"
    DISCLOSURE_REQUIRED = "disclosure_required"
    RESTRICTED = "restricted"


@dataclass(frozen=True)
class GovernanceCap:
    """A caller-supplied, recorded justification for a restricted request.

    Both fields are required and both are meant to be logged verbatim by
    whatever calls this module (a future control plane's admission check,
    Section 6.1) -- this dataclass only carries the values; it does not
    authenticate ``approver`` or validate ``cap`` against anything.
    """

    cap: str
    approver: str


class PolicyRejection(PermissionError):
    """Raised when a restricted request is made without a :class:`GovernanceCap`."""


class MomentReportingViolation(ValueError):
    """Raised when a pooled moment is requested as a point estimate it cannot support."""


@dataclass(frozen=True)
class MetricPolicy:
    name: str
    restriction: MetricRestriction
    reason: str


#: The named metrics the validated region actually gates (architecture plan
#: Section 8.5, `tail_metric_policy.allowed_unrestricted`). Every one of
#: these is validated in BOTH the pooled and horizon-matched families at the
#: canonical 252-day horizon; none of them requires knowing which specific
#: artifact is being asked about.
_NAMED_METRIC_POLICIES: dict[str, MetricPolicy] = {
    name: MetricPolicy(name, MetricRestriction.UNRESTRICTED, reason)
    for name, reason in {
        "VaR 95%": "validated and gated in both estimator families",
        "VaR 99%": "validated and gated in both estimator families",
        "ES 95%": "validated and gated in both estimator families; ES>=VaR by construction",
        "ES 99%": "validated and gated in both estimator families; ES>=VaR by construction",
        "q01": "validated pooled return quantile",
        "q05": "validated pooled return quantile",
        "q95": "validated pooled return quantile",
        "q99": "validated pooled return quantile",
        "volatility": "validated in both estimator families",
        "maximum drawdown": "horizon-matched by construction (AGENTS.md invariant 6)",
        "drawdown median": "validated pooled statistic",
    }.items()
}


def classify_tail_level(level: float) -> MetricRestriction:
    """Classify a quantile/tail-expectation request by its confidence level.

    Mirrors the policy table (Section 8.5) directly:

    * ``level <= 0.99``               -> UNRESTRICTED (the validated, gated region)
    * ``0.99 < level <= 0.995``       -> DISCLOSURE_REQUIRED (inside the historical
      record but past the gated region -- must carry ``extrapolation_disclosure``)
    * ``level > 0.995``               -> RESTRICTED (``extreme_extrapolation_policy``:
      beyond the worst year in a 16-non-overlapping-year record there is nothing
      to calibrate against)

    Independent of any artifact's diagnostics: this boundary is a property
    of the validation methodology (a 252-day horizon, a 16-year record), not
    of which model was fitted.
    """
    if level <= _VALIDATED_LEVEL_CEILING:
        return MetricRestriction.UNRESTRICTED
    if level <= _DISCLOSURE_LEVEL_CEILING:
        return MetricRestriction.DISCLOSURE_REQUIRED
    return MetricRestriction.RESTRICTED


def classify_named_metric(metric: str) -> MetricRestriction:
    """Classify one of the fixed, always-unrestricted named metrics.

    Raises ``KeyError`` for a name outside the fixed registry -- callers
    asking about a tail level/percentage (e.g. "the 99.9% tail expectation")
    should use :func:`classify_tail_level` instead, which is what
    :func:`check_metric_request` does for a level-shaped request.
    """
    return _NAMED_METRIC_POLICIES[metric].restriction


def check_metric_request(
    metric: str,
    *,
    level: float | None = None,
    governance: GovernanceCap | None = None,
) -> MetricRestriction:
    """Enforce policy for one metric request; raise :class:`PolicyRejection` if restricted.

    Two ways to call this, matching the two things the policy table
    classifies:

    * a named metric (``metric="VaR 99%"``, ``level=None``) -- looked up in
      the fixed registry;
    * a confidence-level request (``metric="tail expectation"`` or similar
      free-form label, ``level=0.999``) -- classified by
      :func:`classify_tail_level`.

    Returns the resolved :class:`MetricRestriction` on success (including
    ``DISCLOSURE_REQUIRED``, which is not an error -- the caller is expected
    to attach ``extrapolation_disclosure=True`` to its own response).
    """
    restriction = (
        classify_named_metric(metric) if level is None else classify_tail_level(level)
    )
    if restriction is MetricRestriction.RESTRICTED and governance is None:
        label = metric if level is None else f"{metric} at level={level}"
        raise PolicyRejection(
            f"{label!r} is restricted under extreme_extrapolation_policy "
            f"(policy_set_version={POLICY_SET_VERSION}): beyond the worst year in "
            "a 16-non-overlapping-year record there is nothing to calibrate "
            "against, and the fitted process has no finite unconditional fourth "
            "moment, so extrapolation there is unusually heavy model risk. "
            "Supply a GovernanceCap(cap=..., approver=...) to request it."
        )
    return restriction


def check_pooled_moment_point_estimate(
    moment: str,
    diagnostics: StructuralDiagnostics,
) -> None:
    """Raise if ``moment`` may not be reported as an ordinary point estimate.

    ``moment`` must be ``"skewness"`` or ``"excess_kurtosis"``. Reads
    ``diagnostics.finite_third_moment`` / ``finite_fourth_moment`` -- facts
    computed once at calibration (Section 8.6) -- and never recomputes or
    second-guesses them here. AGENTS.md invariant 20 already requires these
    two statistics to be quoted as a cross-seed median rather than a single
    realisation *because* they have no finite population value to converge
    to; this function is the machine-checkable form of that rule, and the
    horizon-matched block estimates of the same quantities are explicitly
    unaffected (Section 8.5's ``moment_reporting_policy`` note): they ARE
    stable across seeds, because the block estimator is what the validation
    tolerance was derived under.
    """
    needs_finite = {
        "skewness": diagnostics.finite_third_moment,
        "excess_kurtosis": diagnostics.finite_fourth_moment,
    }
    if moment not in needs_finite:
        raise KeyError(
            f"unknown pooled moment {moment!r}; expected 'skewness' or 'excess_kurtosis'"
        )
    if not needs_finite[moment]:
        raise MomentReportingViolation(
            f"pooled {moment} has no finite population value under this artifact's "
            f"fitted process (implied return tail index "
            f"{diagnostics.implied_return_tail_index:.3f}); report a cross-seed "
            "median together with the non-existence flag instead (AGENTS.md "
            f"invariant 20; policy_set_version={POLICY_SET_VERSION})"
        )
