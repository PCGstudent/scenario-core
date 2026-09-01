"""Offline end-to-end smoke test.

The pipeline is exercised with a fabricated price series so that integration breaks
are caught without network access to Yahoo Finance.
"""

import json

import numpy as np
import pandas as pd
import pytest

from xtra_takehome import __main__ as pipeline
from xtra_takehome.config import Config


@pytest.fixture
def fake_close() -> pd.Series:
    """A volatility-clustered price series long enough to pass the data guard."""
    rng = np.random.default_rng(11)
    n = 2_600
    variance = np.empty(n)
    returns = np.empty(n)
    variance[0] = 4.0
    for t in range(n):
        if t > 0:
            variance[t] = 0.06 + 0.08 * returns[t - 1] ** 2 + 0.90 * variance[t - 1]
        returns[t] = np.sqrt(variance[t]) * rng.standard_t(df=6) / np.sqrt(6 / 4)
    prices = 70.0 * np.exp(np.cumsum(returns / 100.0))
    index = pd.bdate_range("2010-01-04", periods=n)
    return pd.Series(prices, index=index, name="close")


def test_pipeline_produces_every_declared_artefact(tmp_path, monkeypatch, fake_close):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_close", lambda *a, **k: fake_close)
    # A smaller simulation keeps the smoke test fast; the code path is identical.
    monkeypatch.setattr(pipeline, "Config", lambda: Config(n_paths=40, max_acf_lag=5))

    pipeline.main()

    reports = tmp_path / "reports"
    for name in (
        "validation_report.md",
        "fit_summary.txt",
        "run_manifest.json",
    ):
        assert (reports / name).is_file(), name

    for figure in (
        "diagnostics_acf.png",
        "tail_qq_student_t.png",
        "tail_index_and_mean_excess.png",
        "marginal_comparison.png",
        "squared_acf_real_vs_synthetic.png",
        "year_severity.png",
        "drawdown_distribution.png",
    ):
        assert (reports / "figures" / figure).stat().st_size > 0, figure


def test_manifest_records_both_gate_families_and_the_rng_scheme(
    tmp_path, monkeypatch, fake_close
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_close", lambda *a, **k: fake_close)
    monkeypatch.setattr(pipeline, "Config", lambda: Config(n_paths=40, max_acf_lag=5))

    pipeline.main()
    manifest = json.loads((tmp_path / "reports" / "run_manifest.json").read_text())

    validation = manifest["validation"]
    assert validation["pooled_marginal"]["total_gates"] == 15
    assert validation["horizon_matched"]["total_gates"] == 15
    assert validation["horizon_matched"]["independent_years"] >= 1
    assert set(validation["worst_year_exceedance"]) == {
        "volatility",
        "VaR 99%",
        "ES 99%",
        "maximum drawdown",
    }
    assert "SeedSequence" in manifest["simulation"]["rng"]
    assert manifest["model"]["parameters"]["implied_unconditional_volatility"] > 0


def test_report_states_the_failure_mode_and_both_families(
    tmp_path, monkeypatch, fake_close
):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(pipeline, "fetch_close", lambda *a, **k: fake_close)
    monkeypatch.setattr(pipeline, "Config", lambda: Config(n_paths=40, max_acf_lag=5))

    pipeline.main()
    text = (tmp_path / "reports" / "validation_report.md").read_text(encoding="utf-8")

    assert "Family 1: pooled marginal gates" in text
    assert "Family 2: horizon-matched year-level gates" in text
    assert "Honest failure mode" in text
    assert "Hill estimator" in text
    assert "Poisson" in text
    # The report must never present the two families as differing in tolerance.
    assert "same declared table" in text
