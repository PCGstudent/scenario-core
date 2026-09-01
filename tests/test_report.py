import numpy as np
import pandas as pd

from xtra_takehome.challenger import GjrSkewTParams
from xtra_takehome.diagnostics import DiagnosticSummary
from xtra_takehome.report import write_report
from xtra_takehome.validation import (
    ExceedanceCheck,
    Gate,
    explosive_path_sensitivity,
    validate_horizon_matched,
)
from xtra_takehome.windows import compute_window_stats, stress_episode_span

HORIZON = 40


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


def _fixture(tmp_path):
    rng = np.random.default_rng(0)
    real_paths = rng.standard_t(df=5, size=(120, HORIZON))
    synthetic_paths = rng.standard_t(df=5, size=(80, HORIZON))
    real_stats = compute_window_stats(real_paths, acf_lags=5)
    block_starts = pd.bdate_range("2015-01-01", periods=real_stats.n_blocks)
    synthetic_stats = compute_window_stats(synthetic_paths, acf_lags=5)

    gates = [
        Gate(
            metric="volatility",
            real=2.0,
            synthetic=2.3,
            error=0.15,
            threshold=0.10,
            error_type="relative",
            passed=False,
        )
    ]
    exceedances = [
        ExceedanceCheck(
            statistic="ES 99%",
            historical_max=23.3,
            synthetic_exceedance_probability=0.02,
            independent_years=16,
            implied_expected_count=0.32,
            lower_count=0.05,
            upper_count=4.74,
            passed=True,
        )
    ]

    out = tmp_path / "validation_report.md"
    write_report(
        out,
        summary=_summary(),
        params=_params(),
        gates=gates,
        matched_gates=validate_horizon_matched(real_stats, synthetic_stats),
        exceedances=exceedances,
        real_stats=real_stats,
        synthetic_stats=synthetic_stats,
        leave_out=explosive_path_sensitivity(
            synthetic_paths, synthetic_stats.volatility, drops=(0, 1, 5)
        ),
        real_moments=(2.0, -0.8, 10.0),
        real_max_abs_return=27.9,
        synthetic_max_abs_return=212.9,
        stress_episode=stress_episode_span(
            real_stats.es99, block_starts, "ES 99%", quantile=0.95
        ),
        n_returns=3000,
    )
    return out.read_text(encoding="utf-8")


def test_report_names_the_model_and_keeps_the_failure(tmp_path):
    text = _fixture(tmp_path)
    assert "GJR-GARCH(1,1,1)" in text
    assert "effective variance persistence" in text
    assert "FAIL" in text
    assert "fourth-moment" in text or "E[A(z)^2]" in text


def test_report_documents_both_estimator_families(tmp_path):
    text = _fixture(tmp_path)
    assert "Family 1: pooled marginal gates" in text
    assert "Family 2: horizon-matched year-level gates" in text
    assert "same declared table" in text


def test_report_explains_why_the_stressed_region_is_not_gated(tmp_path):
    text = _fixture(tmp_path)
    assert "Poisson" in text
    assert "window overlap" in text
    assert "independent" in text


def test_report_quantifies_the_explosive_path_failure(tmp_path):
    text = _fixture(tmp_path)
    assert "Paths removed" in text
    assert "sensitivity diagnostic, not a proposed fix" in text
    assert "212.9" in text


def test_report_reports_the_tail_index_diagnostics(tmp_path):
    text = _fixture(tmp_path)
    assert "Hill estimator" in text
    assert "mean-excess" in text


def test_report_locates_the_stressed_blocks_in_calendar_time(tmp_path):
    """The 'one episode repeated' claim must be evidenced, not asserted."""
    text = _fixture(tmp_path)
    assert "all begin between" in text
    assert "one crisis, replicated" in text


def test_stress_episode_span_reports_a_contiguous_crisis():
    values = np.concatenate([np.ones(90), np.full(10, 50.0)])
    dates = pd.bdate_range("2019-01-01", periods=values.size)
    episode = stress_episode_span(values, dates, "ES 99%", quantile=0.95)

    # The 95% quantile of this array is 50, so the whole elevated block is selected
    # and its calendar span is contiguous: the signature of one repeated episode.
    assert episode.n_blocks == 10
    assert episode.first_start == str(dates[90].date())
    assert episode.last_start == str(dates[99].date())
    assert episode.distinct_years == (2019,)


def test_stress_episode_span_spreads_when_severity_is_not_one_episode():
    """A generator-like series with scattered extremes must not look contiguous."""
    rng = np.random.default_rng(3)
    values = rng.random(500)
    dates = pd.bdate_range("2015-01-01", periods=values.size)
    episode = stress_episode_span(values, dates, "ES 99%", quantile=0.95)
    assert len(episode.distinct_years) > 1
