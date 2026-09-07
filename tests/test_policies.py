"""Proves the governance policy model classifies metrics correctly (plan Section 8.5).

The central claim under test: the fitted process's implied return tail
index (~2.696) means VaR and ES are perfectly well-defined and therefore
UNRESTRICTED at both 95% and 99% -- what actually lacks a finite population
value is the pooled skewness/kurtosis point estimate, which is a narrower,
different, and separately-tested fact. A single broad
``far_tail_usable = false`` flag would conflate these; this file exists to
prove the module never does.
"""

from __future__ import annotations

import pytest

from scenario_platform.domain.artifacts import StructuralDiagnostics
from scenario_platform.domain.policies import (
    GovernanceCap,
    MetricRestriction,
    MomentReportingViolation,
    PolicyRejection,
    check_metric_request,
    check_pooled_moment_point_estimate,
    classify_named_metric,
    classify_tail_level,
)


def _diagnostics(**overrides: object) -> StructuralDiagnostics:
    base = dict(
        effective_persistence=0.9934546510047532,
        fourth_moment_coefficient=1.0457202129024177,
        implied_unconditional_variance=8.6,
        implied_return_tail_index=2.6964247720201326,
        hill_tail_index=2.94,
        finite_second_moment=True,
        finite_third_moment=False,
        finite_fourth_moment=False,
    )
    base.update(overrides)
    return StructuralDiagnostics(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# VaR / ES 95% and 99% must NOT be restricted
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("metric", ["VaR 95%", "VaR 99%", "ES 95%", "ES 99%"])
def test_var_and_es_at_95_and_99_are_unrestricted(metric: str):
    assert classify_named_metric(metric) is MetricRestriction.UNRESTRICTED
    assert check_metric_request(metric) is MetricRestriction.UNRESTRICTED


@pytest.mark.parametrize("level", [0.50, 0.90, 0.95, 0.99])
def test_tail_levels_up_to_99_percent_are_unrestricted(level: float):
    assert classify_tail_level(level) is MetricRestriction.UNRESTRICTED
    assert (
        check_metric_request("tail expectation", level=level)
        is MetricRestriction.UNRESTRICTED
    )


# ---------------------------------------------------------------------------
# The plan's worked example: a 99.9% tail expectation IS restricted
# ---------------------------------------------------------------------------


def test_999_percent_tail_expectation_is_restricted():
    assert classify_tail_level(0.999) is MetricRestriction.RESTRICTED
    with pytest.raises(PolicyRejection):
        check_metric_request("tail expectation", level=0.999)


def test_999_percent_tail_expectation_is_permitted_with_an_explicit_governance_cap():
    cap = GovernanceCap(cap="stress-scenario-review-2026Q3", approver="risk-committee")
    restriction = check_metric_request("tail expectation", level=0.999, governance=cap)
    assert restriction is MetricRestriction.RESTRICTED


def test_disclosure_band_between_99_and_995_percent_is_not_an_error():
    """0.99 < level <= 0.995 is disclosure-required, not restricted: it must
    not raise, but a caller is expected to attach a disclosure of its own."""
    assert classify_tail_level(0.993) is MetricRestriction.DISCLOSURE_REQUIRED
    restriction = check_metric_request("tail expectation", level=0.993)
    assert restriction is MetricRestriction.DISCLOSURE_REQUIRED


def test_beyond_995_percent_is_restricted():
    assert classify_tail_level(0.999999) is MetricRestriction.RESTRICTED


# ---------------------------------------------------------------------------
# Pooled skewness / kurtosis point estimates: rejected under this artifact's facts
# ---------------------------------------------------------------------------


def test_pooled_skewness_point_estimate_is_rejected_when_the_fact_says_no_finite_moment():
    diagnostics = _diagnostics(finite_third_moment=False)
    with pytest.raises(MomentReportingViolation):
        check_pooled_moment_point_estimate("skewness", diagnostics)


def test_pooled_excess_kurtosis_point_estimate_is_rejected_when_the_fact_says_no_finite():
    diagnostics = _diagnostics(finite_fourth_moment=False)
    with pytest.raises(MomentReportingViolation):
        check_pooled_moment_point_estimate("excess_kurtosis", diagnostics)


def test_pooled_moment_point_estimate_is_allowed_when_the_fact_says_the_moment_exists():
    """The policy consumes the fact, it never overrides it: flip the
    StructuralDiagnostics field and the same function permits the request --
    proving the rejection above is driven by the fact, not hardcoded."""
    diagnostics = _diagnostics(finite_third_moment=True, finite_fourth_moment=True)
    check_pooled_moment_point_estimate("skewness", diagnostics)  # must not raise
    check_pooled_moment_point_estimate("excess_kurtosis", diagnostics)  # must not raise


def test_unknown_pooled_moment_name_is_rejected():
    with pytest.raises(KeyError):
        check_pooled_moment_point_estimate("mean", _diagnostics())


# ---------------------------------------------------------------------------
# Facts are never conflated with policy: no metric name reads diagnostics for
# VaR/ES/quantile classification.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "metric",
    [
        "VaR 95%",
        "VaR 99%",
        "ES 95%",
        "ES 99%",
        "q01",
        "q05",
        "q95",
        "q99",
        "volatility",
        "maximum drawdown",
        "drawdown median",
    ],
)
def test_named_metric_classification_does_not_take_diagnostics_at_all(metric: str):
    """classify_named_metric's signature takes only a name -- there is no
    diagnostics parameter to pass, by construction, so a far-tail fact about
    one particular artifact cannot silently restrict VaR/ES for every
    artifact. This test exists to catch a future signature change that would
    reintroduce that coupling."""
    import inspect

    signature = inspect.signature(classify_named_metric)
    assert list(signature.parameters) == ["metric"]
    assert classify_named_metric(metric) is MetricRestriction.UNRESTRICTED
