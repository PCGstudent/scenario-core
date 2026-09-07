import numpy as np
import pandas as pd
import pytest

from xtra_takehome.validation import (
    ACF_TOLERANCE_FRACTION,
    matched_sample_reference,
    MEAN_TOLERANCE_STANDARD_ERRORS,
    THRESHOLDS,
    beyond_historical_max_fraction,
    extreme_region_checks,
    leave_out_sensitivity,
    matched_context,
    pooled_context,
    validate,
    validate_horizon_matched,
)
from xtra_takehome.windows import (
    compute_window_stats,
    non_overlapping_blocks,
    rolling_blocks,
)

HORIZON = 60
ACF_LAGS = 5


def _clustered_returns(seed: int = 0, n: int = 2400) -> pd.Series:
    """A GARCH-like series, so the squared-return ACF is genuinely non-zero."""
    rng = np.random.default_rng(seed)
    variance = np.empty(n)
    out = np.empty(n)
    variance[0] = 4.0
    for t in range(n):
        if t > 0:
            variance[t] = 0.05 + 0.10 * out[t - 1] ** 2 + 0.88 * variance[t - 1]
        out[t] = np.sqrt(variance[t]) * rng.standard_t(df=6) / np.sqrt(6 / 4)
    return pd.Series(out)


def _iid_bootstrap(returns: pd.Series, n_paths: int, seed: int = 1) -> np.ndarray:
    """Exact historical marginal, zero volatility clustering."""
    rng = np.random.default_rng(seed)
    return rng.choice(returns.to_numpy(), size=(n_paths, HORIZON), replace=True)


def _context(returns: pd.Series):
    return pooled_context(returns.to_numpy(), acf_lags=ACF_LAGS)


# ---------------------------------------------------------------------------
# The defect this design exists to prevent
# ---------------------------------------------------------------------------


@pytest.mark.invariants
@pytest.mark.negative_controls  # AGENTS.md invariant 14: a gate must be provably able to fail
def test_horizon_matched_acf_gate_rejects_a_generator_with_no_clustering():
    """Regression test for a gate that could not fail.

    Carrying the pooled *absolute* ACF tolerance into the block estimator quietly
    relaxed the gate: the historical target shrinks by roughly a factor of three
    under that estimator, so an iid bootstrap of the real returns -- the exact
    historical marginal with no volatility clustering whatsoever -- scored just
    under the threshold and passed. The tolerance is now a fraction of the
    historical scale under the estimator in use, and this test pins that.
    """
    returns = _clustered_returns()
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    iid_stats = compute_window_stats(
        _iid_bootstrap(returns, n_paths=400), acf_lags=ACF_LAGS
    )

    gates = validate_horizon_matched(
        real_stats, iid_stats, _context(returns).mean_standard_error
    )
    acf_gate = next(g for g in gates if g.metric == "squared-return ACF MAE")
    assert not acf_gate.passed, (
        "a generator with no volatility clustering must fail the squared-return "
        "ACF gate under the block estimator"
    )


@pytest.mark.invariants  # AGENTS.md invariant 15: scale-derived tolerances re-derived per estimator
def test_acf_tolerance_tracks_the_estimator_scale():
    """The tolerance must move with the statistic it is applied to."""
    returns = _clustered_returns()
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    pooled = _context(returns)
    matched = matched_context(real_stats, pooled.mean_standard_error)

    # The block estimator shrinks the historical squared-return ACF substantially.
    assert matched.acf_scale < pooled.acf_scale
    # ...and the tolerance shrinks with it, by exactly the declared fraction.
    assert np.isclose(pooled.acf_threshold, ACF_TOLERANCE_FRACTION * pooled.acf_scale)
    assert np.isclose(matched.acf_threshold, ACF_TOLERANCE_FRACTION * matched.acf_scale)
    assert matched.acf_threshold < pooled.acf_threshold


@pytest.mark.invariants  # AGENTS.md invariant 15: scale-derived tolerances re-derived per estimator
def test_mean_tolerance_is_expressed_in_standard_errors():
    returns = _clustered_returns()
    context = _context(returns)
    expected = MEAN_TOLERANCE_STANDARD_ERRORS * context.mean_standard_error
    assert np.isclose(context.mean_threshold, expected)
    # The mean tolerance describes the record, not the slicing, so it does not
    # change between the two estimator families.
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    assert np.isclose(
        matched_context(real_stats, context.mean_standard_error).mean_threshold,
        context.mean_threshold,
    )


# ---------------------------------------------------------------------------
# Gate plumbing
# ---------------------------------------------------------------------------


@pytest.mark.invariants  # AGENTS.md invariant 15: scale-derived tolerances re-derived per estimator
def test_scale_free_tolerances_are_identical_across_families():
    returns = _clustered_returns()
    paths = _iid_bootstrap(returns, n_paths=200, seed=3)
    pooled, _, _ = validate(returns, paths, horizon=HORIZON, acf_lags=ACF_LAGS)
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    matched = validate_horizon_matched(
        real_stats,
        compute_window_stats(paths, acf_lags=ACF_LAGS),
        _context(returns).mean_standard_error,
    )

    matched_by_name = {g.metric: g for g in matched}
    for gate in pooled:
        if gate.metric not in matched_by_name:
            continue
        other = matched_by_name[gate.metric]
        assert gate.error_type == other.error_type
        if gate.metric in THRESHOLDS:
            assert gate.threshold == other.threshold == THRESHOLDS[gate.metric][1]


def test_gate_pass_flag_follows_error_and_threshold():
    returns = _clustered_returns()
    pooled, _, _ = validate(
        returns, _iid_bootstrap(returns, 200), horizon=HORIZON, acf_lags=ACF_LAGS
    )
    for g in pooled:
        assert g.passed == (g.error <= g.threshold)


def test_drawdowns_are_reported_once_not_duplicated_across_families():
    """They are horizon-matched by construction; repeating them padded both scorecards."""
    returns = _clustered_returns()
    paths = _iid_bootstrap(returns, 200)
    pooled, diagnostics, _ = validate(
        returns, paths, horizon=HORIZON, acf_lags=ACF_LAGS
    )
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    matched = validate_horizon_matched(
        real_stats,
        compute_window_stats(paths, acf_lags=ACF_LAGS),
        _context(returns).mean_standard_error,
    )

    assert "drawdown median" in {g.metric for g in pooled}
    assert not any("drawdown" in g.metric for g in matched)
    # p95 of overlapping historical windows is not identified, so it is reported.
    assert "drawdown p95" in {d.name for d in diagnostics}


@pytest.mark.invariants  # AGENTS.md invariant 13: equal-length blocks on both sides
def test_horizon_matched_requires_equal_block_lengths():
    real = compute_window_stats(np.zeros((5, 30)) + np.arange(30), ACF_LAGS)
    synthetic = compute_window_stats(np.zeros((5, 20)) + np.arange(20), ACF_LAGS)
    with pytest.raises(ValueError):
        validate_horizon_matched(real, synthetic, 0.01)


# ---------------------------------------------------------------------------
# Family 3
# ---------------------------------------------------------------------------


@pytest.mark.invariants
@pytest.mark.negative_controls  # AGENTS.md invariant 14: the extreme-region check must be able to flag
def test_extreme_region_flags_a_generator_that_cannot_reach_the_record():
    returns = _clustered_returns(seed=5)
    disjoint = compute_window_stats(
        non_overlapping_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    tame = compute_window_stats(
        _iid_bootstrap(returns, 400, seed=6) * 0.2, acf_lags=ACF_LAGS
    )
    checks = extreme_region_checks(disjoint, tame)

    assert {c.statistic for c in checks} == {
        "volatility",
        "VaR 99%",
        "ES 99%",
        "maximum drawdown",
    }
    for c in checks:
        assert c.annual_exceedance_probability == 0.0
        # It never reaches the observed extreme, so a record of this length is
        # certain to fall short.
        assert c.probability_below == 1.0
        assert c.flagged


@pytest.mark.invariants  # AGENTS.md invariant 17: matched-length record plausibility, not max-vs-max
def test_extreme_region_does_not_flag_a_generator_from_the_same_process():
    """A second realization of the same process must look plausible.

    Note the contrast with the test above: an iid bootstrap is flagged precisely
    because destroying volatility clustering also destroys the model's ability to
    produce an extreme year, so the check has real power.
    """
    returns = _clustered_returns(seed=7)
    disjoint = compute_window_stats(
        non_overlapping_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    twin = _clustered_returns(seed=8, n=HORIZON * 500).to_numpy()
    twin_stats = compute_window_stats(
        twin.reshape(500, HORIZON), acf_lags=ACF_LAGS
    )
    checks = extreme_region_checks(disjoint, twin_stats)
    assert any(not c.flagged for c in checks)


@pytest.mark.invariants  # AGENTS.md invariant 17: matched-length record plausibility, not max-vs-max
def test_extreme_region_probabilities_are_internally_consistent():
    returns = _clustered_returns(seed=9)
    disjoint = compute_window_stats(
        non_overlapping_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    synthetic = compute_window_stats(
        _iid_bootstrap(returns, 400, seed=10), acf_lags=ACF_LAGS
    )
    for c in extreme_region_checks(disjoint, synthetic):
        assert np.isclose(c.probability_at_least_one, 1.0 - c.probability_below)
        assert np.isclose(
            c.probability_below,
            (1.0 - c.annual_exceedance_probability) ** c.n_blocks,
        )
        assert c.n_blocks == disjoint.n_blocks


# ---------------------------------------------------------------------------
# Leave-out diagnostic
# ---------------------------------------------------------------------------


@pytest.mark.invariants  # AGENTS.md invariant 18: a convicting diagnostic needs the comparator
def test_leave_out_collapses_pooled_moments_for_history_too():
    """The comparator that keeps the diagnostic honest.

    Heavy-tailed data behaves the same way as the generator: reporting only the
    synthetic rows would present a universal property of such samples as a defect.
    """
    returns = _clustered_returns(seed=11)
    blocks = non_overlapping_blocks(returns.to_numpy(), HORIZON)
    stats_ = compute_window_stats(blocks, acf_lags=ACF_LAGS)

    rows = leave_out_sensitivity("historical", blocks, stats_.volatility, drops=(0, 1))
    assert [r.source for r in rows] == ["historical", "historical"]
    assert rows[0].dropped == 0 and rows[1].dropped == 1
    # Dropping the single worst block materially moves the pooled moments.
    assert rows[1].volatility < rows[0].volatility
    assert abs(rows[1].excess_kurtosis) < abs(rows[0].excess_kurtosis)


def test_leave_out_skips_drops_larger_than_the_sample():
    blocks = np.random.default_rng(12).normal(size=(3, HORIZON))
    stats_ = compute_window_stats(blocks, acf_lags=ACF_LAGS)
    rows = leave_out_sensitivity("synthetic", blocks, stats_.volatility, drops=(0, 10))
    assert [r.dropped for r in rows] == [0]


def test_beyond_historical_max_fraction_is_a_share():
    returns = _clustered_returns(seed=13)
    real_stats = compute_window_stats(
        rolling_blocks(returns.to_numpy(), HORIZON), acf_lags=ACF_LAGS
    )
    calm = compute_window_stats(
        _iid_bootstrap(returns, 200, seed=14) * 0.1, acf_lags=ACF_LAGS
    )
    assert beyond_historical_max_fraction(real_stats, calm) == 0.0


class _RecordingGenerator:
    """Records the shape asked of it, then returns iid noise of that shape."""

    def __init__(self):
        self.calls: list[tuple[int, int]] = []

    def simulate(self, n_steps: int, n_paths: int, seed: int) -> np.ndarray:
        self.calls.append((n_steps, n_paths))
        return np.random.default_rng(seed).standard_t(df=5, size=(n_paths, n_steps))


@pytest.mark.invariants  # AGENTS.md invariant 5: never build records by concatenating paths
def test_matched_reference_simulates_whole_records_not_stitched_years():
    """The section claims records of the historical length; it must build them.

    An earlier version concatenated independent 252-day paths, which resets the
    conditional variance every year and drops any volatility episode crossing a
    year boundary -- with persistence near one that is not a harmless difference.
    """
    returns = _clustered_returns(seed=15, n=1000).to_numpy()
    generator = _RecordingGenerator()

    references = matched_sample_reference(
        generator, returns, records_per_seed=5, seeds=(1, 2)
    )

    assert generator.calls == [(1000, 5), (1000, 5)], (
        "each record must be one continuous path of exactly the historical length"
    )
    assert {r.statistic for r in references} == {
        "volatility",
        "skewness",
        "excess kurtosis",
    }
    for r in references:
        assert r.model_p05 <= r.model_median <= r.model_p95
        assert 0.0 <= r.percentile <= 100.0
        assert r.inside == (r.model_p05 <= r.historical <= r.model_p95)
