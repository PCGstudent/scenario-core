import numpy as np
import pandas as pd
import pytest

from xtra_takehome.challenger import GjrSkewTParams
from xtra_takehome.diagnostics import DiagnosticSummary
from xtra_takehome.report import write_report
from xtra_takehome.validation import (
    ExtremeRegionCheck,
    MatchedSampleReference,
    leave_out_sensitivity,
    pooled_context,
    validate,
    validate_horizon_matched,
)
from xtra_takehome.windows import (
    compute_window_stats,
    non_overlapping_blocks,
    rolling_blocks,
    stress_episode_span,
)

HORIZON = 40
ACF_LAGS = 5


def _summary() -> DiagnosticSummary:
    return DiagnosticSummary(
        n=3000,
        mean=0.01,
        std=2.0,
        skew=-0.8,
        excess_kurtosis=10.0,
        min_return=-12.0,
        max_return=9.0,
        q01=-6.0,
        q05=-3.0,
        q95=3.0,
        q99=5.5,
        max_abs_return_acf=0.04,
        mean_abs_squared_acf=0.12,
        student_t_df=4.5,
        student_t_loc=0.0,
        student_t_scale=1.5,
        hill_left=2.9,
        hill_right=3.1,
        hill_k=100,
    )


def _params() -> GjrSkewTParams:
    return GjrSkewTParams(
        mu=0.01, omega=0.05, alpha=0.06, gamma=0.04, beta=0.90, eta=6.0, lam=-0.10
    )


def _fixture(tmp_path) -> str:
    rng = np.random.default_rng(0)
    real = pd.Series(rng.standard_t(df=5, size=1200))
    synthetic = rng.standard_t(df=5, size=(80, HORIZON))

    real_stats = compute_window_stats(
        rolling_blocks(real.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    synthetic_stats = compute_window_stats(synthetic, acf_lags=ACF_LAGS)
    disjoint = non_overlapping_blocks(real.to_numpy(), HORIZON)
    disjoint_stats = compute_window_stats(disjoint, acf_lags=ACF_LAGS)
    context = pooled_context(real.to_numpy(), acf_lags=ACF_LAGS)

    gates, diagnostics, extras = validate(
        real, synthetic, horizon=HORIZON, acf_lags=ACF_LAGS
    )
    matched = validate_horizon_matched(
        real_stats, synthetic_stats, context.mean_standard_error
    )

    extremes = [
        ExtremeRegionCheck(
            statistic="ES 99%",
            historical_max=23.3,
            annual_exceedance_probability=0.023,
            n_blocks=disjoint_stats.n_blocks,
            probability_at_least_one=0.31,
            probability_below=0.69,
            flagged=False,
        )
    ]
    references = [
        MatchedSampleReference(
            statistic="excess kurtosis",
            historical=14.7,
            model_median=9.9,
            model_p05=4.0,
            model_p95=66.0,
            percentile=69.0,
            inside=True,
        )
    ]
    leave_out = leave_out_sensitivity(
        "synthetic", synthetic, synthetic_stats.volatility, drops=(0, 1)
    ) + leave_out_sensitivity(
        "historical", disjoint, disjoint_stats.volatility, drops=(0, 1)
    )

    out = tmp_path / "validation_report.md"
    write_report(
        out,
        summary=_summary(),
        params=_params(),
        gates=gates,
        diagnostics=diagnostics,
        matched_gates=matched,
        extremes=extremes,
        references=references,
        real_stats=real_stats,
        synthetic_stats=synthetic_stats,
        leave_out=leave_out,
        beyond_max_fraction=0.044,
        acf_floor=(0.0029, 0.0045),
        acf_scale=(
            float(extras["acf_scale"]),
            float(np.mean(np.abs(real_stats.mean_squared_acf[1:]))),
        ),
        stress_episode=stress_episode_span(
            real_stats.es99,
            pd.bdate_range("2015-01-01", periods=real_stats.n_blocks),
            "ES 99%",
            quantile=0.95,
        ),
        n_returns=1200,
    )
    return out.read_text(encoding="utf-8")


def test_report_names_the_model_and_its_structural_diagnostic(tmp_path):
    text = _fixture(tmp_path)
    assert "GJR-GARCH(1,1,1)" in text
    assert "effective variance persistence" in text
    assert "E[A(z)^2]" in text


def test_report_documents_both_estimator_families(tmp_path):
    text = _fixture(tmp_path)
    assert "Family 1: pooled marginal gates" in text
    assert "Family 2: horizon-matched year-level gates" in text


@pytest.mark.invariants  # AGENTS.md invariant 12: a tolerance correction must be disclosed
def test_report_discloses_the_scale_dependent_tolerances(tmp_path):
    """The unfailable-gate defect must be stated, not silently fixed."""
    text = _fixture(tmp_path)
    assert "could not fail" in text
    assert "fraction of the historical scale" in text
    assert "standard errors of the historical mean" in text


@pytest.mark.invariants  # AGENTS.md invariant 16: don't gate on an unidentified quantile
def test_report_explains_why_the_stressed_region_is_not_gated(tmp_path):
    text = _fixture(tmp_path)
    assert "window overlap" in text
    assert "one crisis, replicated" in text
    assert "measures the simulation budget" in text
    assert "non-overlapping" in text


def test_report_carries_the_matched_length_reference(tmp_path):
    text = _fixture(tmp_path)
    assert "plausible draw" in text
    assert "Model 5-95% band" in text


@pytest.mark.invariants  # AGENTS.md invariant 18: a convicting diagnostic needs the comparator
def test_leave_out_table_includes_the_historical_comparator(tmp_path):
    """Without the historical rows the table would convict the model unfairly."""
    text = _fixture(tmp_path)
    assert "| historical |" in text
    assert "| synthetic |" in text
    assert "Both sides collapse" in text


def test_report_does_not_claim_the_recursion_diverges(tmp_path):
    """An earlier draft called an extreme path evidence of divergence.

    The variance recursion is second-moment stationary, so that was wrong: the
    finding is about unvalidatable extrapolation, not divergence.
    """
    lowered = _fixture(tmp_path).lower()
    assert "recursion diverging" not in lowered
    assert "explosive" not in lowered
    assert "extrapolation" in lowered


def test_report_reports_the_tail_index_diagnostics(tmp_path):
    text = _fixture(tmp_path)
    assert "Hill estimator" in text
    assert "mean-excess" in text
    assert "implied return tail index" in text


def test_report_does_not_print_a_meaningless_zero_for_the_acf_real_column(tmp_path):
    text = _fixture(tmp_path)
    rows = [
        line
        for line in text.splitlines()
        if line.startswith("| squared-return ACF MAE")
    ]
    assert rows
    for row in rows:
        assert "| 0.0000 |" not in row
