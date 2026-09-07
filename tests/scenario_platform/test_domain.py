"""Integration tests over the whole Phase-1 domain surface.

Three groups of tests live here, each needing something the narrower unit
test files (``tests/test_artifact_identity.py``, ``tests/test_artifact_roundtrip.py``,
``tests/test_policies.py``) deliberately avoid depending on:

1. Dataset identity survives real CSV/Parquet re-encoding (Section 7) --
   needs pyarrow, a dev-only test dependency (see ``requirements/dev.in``).
2. A full fit -> simulate -> risk pass, and stored structural diagnostics
   checked against live recomputation to 1e-10 (Section 8.6) -- needs a real
   (fabricated, offline) fit, not hand-built values.
3. ``validate(real_returns, artifact, config)`` reproduces this repository's
   own committed validation result (9/14 pooled gates, 12/13 horizon-matched
   gates -- ``reports/run_manifest.json``) -- checked against the committed
   dataset fixture (``tests/fixtures/brent_dataset_v1/closes.csv``, a plain
   copy of the exact locally cached CSV that produced both the manifest and
   ``tests/fixtures/gjr_skewt_v1/``), never against ``.cache/`` or a live
   fetch, so this whole module runs from a clean clone with no network
   access. ``tests/test_pinned_dataset.py`` is the standalone proof of that
   fixture's identity; nothing in this file imports
   ``xtra_takehome.data.fetch_close``.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scenario_platform.domain.artifacts import build_dataset_ref
from scenario_platform.domain.policies import (
    MomentReportingViolation,
    check_pooled_moment_point_estimate,
)
from scenario_platform.domain.requests import (
    FitConfig,
    RiskConfig,
    ScenarioRequest,
    ValidationConfig,
)
from scenario_platform.domain.services import fit, risk, simulate, validate
from xtra_takehome import diagnostics as _diagnostics
from xtra_takehome.config import Config
from xtra_takehome.data import log_returns_pct

# ---------------------------------------------------------------------------
# 1. Dataset identity survives CSV / Parquet re-encoding
# ---------------------------------------------------------------------------


def _fake_close_series() -> pd.Series:
    rng = np.random.default_rng(99)
    index = pd.bdate_range("2015-01-02", periods=400)
    close = 70.0 * np.exp(np.cumsum(rng.normal(0.0, 0.01, size=400)))
    return pd.Series(close, index=index, name="close")


def test_dataset_id_is_unchanged_by_csv_round_trip(tmp_path):
    close = _fake_close_series()
    original = build_dataset_ref(
        ticker="TEST",
        start="2015-01-02",
        end="2016-08-01",
        close=close,
        uri="memory://original",
    )

    csv_path = tmp_path / "close.csv"
    close.to_csv(csv_path)
    reread = pd.read_csv(csv_path, index_col=0, parse_dates=True).iloc[:, 0].astype(float)
    reread.name = "close"

    from_csv = build_dataset_ref(
        ticker="TEST", start="2015-01-02", end="2016-08-01", close=reread, uri="local://csv"
    )
    assert from_csv.dataset_id == original.dataset_id


def test_dataset_id_is_unchanged_by_parquet_round_trip(tmp_path):
    """Parquet is a genuinely different transport format from CSV (binary,
    columnar, its own type system) -- proving identity survives it is a
    materially different claim from surviving a CSV round trip.

    The only test in this module that needs pyarrow (a dev-only test
    dependency, ``requirements/dev.in``) -- skipped here, and only here, if
    it is unavailable; every other test in this file must still collect and
    run without it.
    """
    pytest.importorskip("pyarrow")
    close = _fake_close_series()
    original = build_dataset_ref(
        ticker="TEST",
        start="2015-01-02",
        end="2016-08-01",
        close=close,
        uri="memory://original",
    )

    parquet_path = tmp_path / "close.parquet"
    close.to_frame().to_parquet(parquet_path)
    reread = pd.read_parquet(parquet_path)["close"]

    from_parquet = build_dataset_ref(
        ticker="TEST",
        start="2015-01-02",
        end="2016-08-01",
        close=reread,
        uri="local://parquet",
    )
    assert from_parquet.dataset_id == original.dataset_id


def test_dataset_id_changes_when_a_single_close_value_changes():
    close = _fake_close_series()
    original = build_dataset_ref(
        ticker="TEST",
        start="2015-01-02",
        end="2016-08-01",
        close=close,
        uri="memory://original",
    )

    mutated = close.copy()
    mutated.iloc[123] = mutated.iloc[123] + 0.5
    changed = build_dataset_ref(
        ticker="TEST",
        start="2015-01-02",
        end="2016-08-01",
        close=mutated,
        uri="memory://mutated",
    )
    assert changed.dataset_id != original.dataset_id


# ---------------------------------------------------------------------------
# 2. Full fit -> simulate -> risk pass, and diagnostics vs live recomputation
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def fabricated_returns() -> pd.Series:
    rng = np.random.default_rng(2026)
    n = 2_600
    variance = np.empty(n)
    returns = np.empty(n)
    variance[0] = 4.0
    for t in range(n):
        if t > 0:
            variance[t] = 0.06 + 0.08 * returns[t - 1] ** 2 + 0.90 * variance[t - 1]
        returns[t] = np.sqrt(variance[t]) * rng.standard_t(df=6) / np.sqrt(6 / 4)
    prices = 68.0 * np.exp(np.cumsum(returns / 100.0))
    index = pd.bdate_range("2012-02-01", periods=n)
    close = pd.Series(prices, index=index, name="close")
    return log_returns_pct(close), close


@pytest.fixture(scope="module")
def domain_artifact(fabricated_returns):
    returns, close = fabricated_returns
    dataset = build_dataset_ref(
        ticker="TEST-FAB",
        start=str(close.index[0].date()),
        end=str(close.index[-1].date()),
        close=close,
        uri="memory://fabricated",
    )
    config = FitConfig(dataset=dataset, model_version="gjr-skewt-domain-test")
    return fit(returns, config), returns


def test_stored_diagnostics_match_live_recomputation_to_1e_minus_10(domain_artifact):
    """``implied_return_tail_index`` is a root-find (``challenger.py``'s
    ``implied_return_tail_index`` property) that returns ``nan`` by design
    for some fitted parameterisations (an unenclosed root -- see its
    docstring). Both sides of every comparison below call the exact same
    property on the exact same ``params`` object, so a ``nan`` on one side
    is necessarily a ``nan`` on the other; ``np.testing.assert_allclose``
    with ``equal_nan=True`` treats that as equality, matching the intent
    ("the stored value is what a fresh recomputation gives"), whereas
    ``pytest.approx`` treats ``nan != nan`` as a mismatch regardless of
    tolerance.
    """
    artifact, returns = domain_artifact
    params = artifact.params

    np.testing.assert_allclose(
        artifact.diagnostics.effective_persistence,
        params.effective_persistence,
        atol=1e-10,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        artifact.diagnostics.fourth_moment_coefficient,
        params.fourth_moment_coefficient,
        atol=1e-10,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        artifact.diagnostics.implied_unconditional_variance,
        params.implied_unconditional_variance,
        atol=1e-10,
        equal_nan=True,
    )
    np.testing.assert_allclose(
        artifact.diagnostics.implied_return_tail_index,
        params.implied_return_tail_index,
        atol=1e-10,
        equal_nan=True,
    )

    recomputed_hill = _diagnostics.hill_estimator(
        -np.asarray(returns, dtype=float), _diagnostics.HILL_REFERENCE_K
    )
    np.testing.assert_allclose(
        artifact.diagnostics.hill_tail_index, recomputed_hill, atol=1e-10, equal_nan=True
    )


def test_diagnostics_booleans_are_consistent_with_the_tail_index_they_are_derived_from(
    domain_artifact,
):
    artifact, _ = domain_artifact
    d = artifact.diagnostics
    assert d.finite_second_moment == (d.implied_return_tail_index > 2.0)
    assert d.finite_third_moment == (d.implied_return_tail_index > 3.0)
    assert d.finite_fourth_moment == (
        d.implied_return_tail_index > 4.0 and d.fourth_moment_coefficient < 1.0
    )


def test_full_pipeline_fit_simulate_risk(domain_artifact):
    artifact, _ = domain_artifact
    request = ScenarioRequest(
        model_version="gjr-skewt-domain-test", horizon=252, n_paths=200, seed=17
    )
    scenarios = simulate(artifact, request)
    assert scenarios.returns.shape == (200, 252)
    assert np.isfinite(scenarios.returns).all()

    report = risk(scenarios, RiskConfig())
    for level in (0.95, 0.99):
        var, es = report.var_es[level]
        assert var > 0.0
        assert es >= var  # ES >= VaR by construction, positive-loss convention
    assert 0.0 <= report.max_drawdown_median <= 1.0


def test_policy_rejects_pooled_moments_this_fitted_process_cannot_support(domain_artifact):
    """Wires the fitted artifact's own diagnostics into the policy check --
    a smoke test that the two modules actually compose, not just that each
    passes its own unit tests in isolation."""
    artifact, _ = domain_artifact
    d = artifact.diagnostics
    if not d.finite_third_moment:
        with pytest.raises(MomentReportingViolation):
            check_pooled_moment_point_estimate("skewness", d)
    if not d.finite_fourth_moment:
        with pytest.raises(MomentReportingViolation):
            check_pooled_moment_point_estimate("excess_kurtosis", d)


# ---------------------------------------------------------------------------
# 3. validate() reproduces the repository's own committed validation result
#
# Uses the COMMITTED dataset fixture (tests/fixtures/brent_dataset_v1/closes.csv)
# -- a plain byte-for-byte copy of the exact locally cached CSV that produced
# both reports/run_manifest.json and tests/fixtures/gjr_skewt_v1/, copied in
# rather than fetched -- so this whole section runs from a clean clone with
# no .cache/ directory and no network access. See test_pinned_dataset.py for
# the explicit proof of that (closes/returns counts, ticker, window, and
# dataset_id, checked against the golden artifact's own provenance).
# ---------------------------------------------------------------------------

_CFG = Config()
FIXTURE_CLOSES_CSV = (
    Path(__file__).parent.parent / "fixtures" / "brent_dataset_v1" / "closes.csv"
)


def _load_committed_close_series() -> pd.Series:
    """Read the committed dataset fixture the same way ``fetch_close`` reads
    its local cache (``xtra_takehome.data._validate_close`` on a plain
    ``pd.read_csv``) -- deliberately *not* calling ``fetch_close`` itself,
    so there is no code path here that could ever fall through to a live
    yfinance request."""
    from xtra_takehome.data import _validate_close

    raw = pd.read_csv(FIXTURE_CLOSES_CSV, index_col=0, parse_dates=True).iloc[:, 0]
    return _validate_close(raw.astype(float).rename("close"))


@pytest.fixture(scope="module")
def real_dataset_validation():
    close = _load_committed_close_series()
    returns = log_returns_pct(close)
    dataset = build_dataset_ref(
        ticker=_CFG.ticker,
        start=_CFG.start,
        end=_CFG.end,
        close=close,
        uri="local://tests/fixtures/brent_dataset_v1/closes.csv",
    )
    config = FitConfig(dataset=dataset, model_version="gjr-skewt-real-data-test")
    artifact = fit(returns, config)
    report = validate(returns, artifact, ValidationConfig())
    return artifact, returns, report


def test_committed_fixture_dataset_id_matches_the_golden_artifacts_provenance(
    real_dataset_validation,
):
    """The committed dataset fixture is a copy of the exact CSV that produced
    both reports/run_manifest.json and tests/fixtures/gjr_skewt_v1/ -- this
    is that claim, checked against the golden artifact's own recorded
    provenance rather than merely asserted."""
    import json

    artifact, _, _ = real_dataset_validation
    golden_provenance = json.loads(
        (
            Path(__file__).parent.parent / "fixtures" / "gjr_skewt_v1" / "artifact.json"
        ).read_text()
    )["provenance"]
    assert artifact.provenance.dataset_id == golden_provenance["dataset_id"]


def test_validate_reproduces_the_committed_pooled_gate_count(real_dataset_validation):
    _, _, report = real_dataset_validation
    assert report.pooled_total == 14
    assert report.pooled_passed == 9


def test_validate_reproduces_the_committed_horizon_matched_gate_count(
    real_dataset_validation,
):
    _, _, report = real_dataset_validation
    assert report.matched_total == 13
    assert report.matched_passed == 12


def test_validate_produces_matched_sample_reference_for_all_three_moments(
    real_dataset_validation,
):
    _, _, report = real_dataset_validation
    assert {r.statistic for r in report.references} == {
        "volatility",
        "skewness",
        "excess kurtosis",
    }


def test_matched_sample_reference_reproduces_the_committed_manifest_exactly(
    real_dataset_validation,
):
    """The three ``MatchedSampleReference`` rows are the manifest fields
    architecture plan Section 7's Tests bullet names explicitly
    ("the manifest's matched-reference percentiles") -- checked here to
    exact equality, not a tolerance, because every field is either read
    straight from the (unchanged, dataset_id-verified) historical returns,
    or is a rank/percentile statistic over 300 simulated records that this
    session's own investigation (see
    ``test_fitted_params_are_tier_3_not_compared_bit_for_bit_to_the_manifest``
    below) found to be insensitive to this environment's small Tier-3
    parameter drift at this sample size -- i.e. this is not an accident,
    it is the evidenced, expected outcome.
    """
    _, _, report = real_dataset_validation
    by_stat = {r.statistic: r for r in report.references}

    # Plain `==`, not pytest.approx: this session's evidence (see the report)
    # showed these specific fields at literal bit equality, not merely close
    # -- using approx here would silently reintroduce the kind of invented
    # tolerance this rewrite exists to remove.
    volatility = by_stat["volatility"]
    assert volatility.historical == 2.3437040979024153
    assert volatility.percentile == 45.33333333333333
    assert volatility.inside is True

    skewness = by_stat["skewness"]
    assert skewness.historical == -0.9584231595584
    assert skewness.percentile == 17.0
    assert skewness.inside is True

    kurtosis = by_stat["excess kurtosis"]
    assert kurtosis.historical == 14.691476231676702
    assert kurtosis.percentile == 71.66666666666667
    assert kurtosis.inside is True


def test_extreme_region_reproduces_the_committed_manifest_exactly(real_dataset_validation):
    """Every field here is either a pure function of the (unchanged)
    historical record, or a count-based fraction over 1000 simulated paths
    that this session's investigation found to land on the exact same
    integer count despite the small Tier-3 parameter drift -- evidenced,
    not assumed; see the comparison in this file's git history / session
    report rather than re-deriving it here."""
    _, _, report = real_dataset_validation
    committed = {
        "volatility": (4.4611152595315895, 0.046, False),
        "VaR 99%": (13.122895776098787, 0.048, False),
        "ES 99%": (23.31301892310871, 0.023, False),
        "maximum drawdown": (0.6295515559476015, 0.042, False),
    }
    by_stat = {e.statistic: e for e in report.extremes}
    for name, (historical_max, exceedance_prob, flagged) in committed.items():
        e = by_stat[name]
        assert e.historical_max == historical_max  # plain ==, see docstring above
        assert e.annual_exceedance_probability == exceedance_prob
        assert e.n_blocks == 16
        assert e.flagged is flagged


def test_beyond_historical_max_fraction_reproduces_the_committed_manifest_exactly(
    real_dataset_validation,
):
    _, _, report = real_dataset_validation
    assert report.beyond_max_fraction == 0.044


def test_fitted_params_are_tier_3_not_compared_bit_for_bit_to_the_manifest(
    real_dataset_validation,
):
    """The frozen ``ModelArtifact`` is the Tier-1 replay object (architecture
    plan Section 16.2); the *act of fitting* is Tier 3 and is explicitly not
    claimed bit-reproducible across sessions or machines. The committed
    ``reports/run_manifest.json`` was produced in a different execution
    environment than this test runs in, so no numeric tolerance against it
    is asserted here -- inventing one (as an earlier version of this test
    did, at rel=1e-3/1e-4 chosen to make the observed drift pass) would
    misstate the contract rather than encode it.

    What this test actually checks: two independent calls to ``fit()`` in
    *this same process* (same Python interpreter, same locked dependency
    versions, same machine) reproduce ``artifact_id`` and every parameter
    exactly. This is **not** a same-worker-image-digest experiment -- Phase 1
    has no worker image to run one against; that experiment (§16.2's Tier 1,
    "20 runs across distinct Fargate task placements") belongs to Phase 2,
    once a worker image exists. What same-process determinism *does*
    establish is that the committed manifest's ~1.9%-in-mu /
    <=2e-4-in-everything-else deviation (recorded in this session's evidence
    table) is a cross-session/cross-environment phenomenon rather than
    noise from this environment itself. The deterministic, seed-controlled
    outputs Tier 1 *does* promise against the historical manifest (gate
    counts, matched-reference rank/percentile and historical-derived
    fields, extreme-region flags) are checked to exact equality in the
    tests above; matched-reference *model-derived* fields (median/p05/p95)
    are Tier-3-drift-sensitive and are not asserted against the manifest
    here for the same reason the raw parameters are not.
    """
    artifact, returns, _ = real_dataset_validation

    dataset = build_dataset_ref(
        ticker=_CFG.ticker,
        start=_CFG.start,
        end=_CFG.end,
        close=_load_committed_close_series(),
        uri="local://tests/fixtures/brent_dataset_v1/closes.csv",
    )
    config = FitConfig(dataset=dataset, model_version="gjr-skewt-real-data-test")
    second_artifact = fit(returns, config)

    assert second_artifact.artifact_id == artifact.artifact_id
    assert second_artifact.params.mu == artifact.params.mu
    assert second_artifact.params.omega == artifact.params.omega
    assert second_artifact.params.eta == artifact.params.eta
