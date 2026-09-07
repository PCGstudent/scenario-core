"""Proves the artifact save/load cycle is faithful, fail-closed, and consequential.

Three things this file establishes, none of which the pure-encoding tests in
``tests/test_artifact_identity.py`` can (they never call ``fit``/``simulate``
or touch disk):

1. ``fit -> save -> load -> simulate`` is *bit-identical* to ``fit ->
   simulate`` for the same request and seed (architecture plan Section 16,
   Tier 1). This is the load-bearing claim behind treating a serialized
   artifact as a faithful stand-in for the in-memory generator that produced
   it.
2. Transport bytes really are irrelevant to identity, and a load fails
   closed the moment stored content and stored id disagree
   (``ArtifactIntegrityError`` -- Section 8.3).
3. Negative control: a "params-only" reconstruction -- exactly what
   AGENTS.md invariant 27 forbids ``ModelArtifact`` from being -- cannot even
   attempt the model's default initialisation, because ``historical_mix``
   samples from state arrays a params-only object never kept.

Uses a small fabricated, volatility-clustered price series (the same
technique as ``tests/test_end_to_end.py``'s ``fake_close``) so this file
needs no network access and no local data cache.
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from scenario_platform.domain import serialization
from scenario_platform.domain.artifacts import ArtifactIntegrityError, build_dataset_ref
from scenario_platform.domain.requests import FitConfig, ScenarioRequest
from scenario_platform.domain.services import fit, simulate
from xtra_takehome.challenger import GjrSkewTGenerator
from xtra_takehome.data import log_returns_pct


@pytest.fixture(scope="module")
def fake_close() -> pd.Series:
    rng = np.random.default_rng(7)
    n = 2_600
    variance = np.empty(n)
    returns = np.empty(n)
    variance[0] = 4.0
    for t in range(n):
        if t > 0:
            variance[t] = 0.06 + 0.08 * returns[t - 1] ** 2 + 0.90 * variance[t - 1]
        returns[t] = np.sqrt(variance[t]) * rng.standard_t(df=6) / np.sqrt(6 / 4)
    prices = 65.0 * np.exp(np.cumsum(returns / 100.0))
    index = pd.bdate_range("2011-01-03", periods=n)
    return pd.Series(prices, index=index, name="close")


@pytest.fixture(scope="module")
def fitted_artifact(fake_close: pd.Series):
    dataset = build_dataset_ref(
        ticker="BZ=F-TEST",
        start=str(fake_close.index[0].date()),
        end=str(fake_close.index[-1].date()),
        close=fake_close,
        uri="memory://fake_close",
    )
    config = FitConfig(dataset=dataset, model_version="gjr-skewt-test-fixture")
    returns = log_returns_pct(fake_close)
    return fit(returns, config)


@pytest.fixture()
def scenario_request() -> ScenarioRequest:
    return ScenarioRequest(
        model_version="gjr-skewt-test-fixture", horizon=60, n_paths=25, seed=4242
    )


# ---------------------------------------------------------------------------
# Proof 1: fit -> save -> load -> simulate is bit-identical to fit -> simulate
# ---------------------------------------------------------------------------


def test_simulate_after_reload_is_bit_identical_to_simulate_before_save(
    tmp_path, fitted_artifact, scenario_request
):
    direct = simulate(fitted_artifact, scenario_request)

    directory = tmp_path / "artifact"
    serialization.save_artifact(fitted_artifact, directory)
    reloaded_artifact = serialization.load_artifact(directory)

    assert reloaded_artifact.artifact_id == fitted_artifact.artifact_id
    reloaded = simulate(reloaded_artifact, scenario_request)

    np.testing.assert_array_equal(direct.returns, reloaded.returns)
    assert direct.returns.tobytes() == reloaded.returns.tobytes()


def test_simulate_after_reload_is_bit_identical_across_initial_state_modes(
    tmp_path, fitted_artifact
):
    """The same proof again for 'latest' and an explicit initial state, so the
    bit-identity claim is not accidentally specific to historical_mix."""
    directory = tmp_path / "artifact"
    serialization.save_artifact(fitted_artifact, directory)
    reloaded_artifact = serialization.load_artifact(directory)

    for initial_state in ("latest", (0.4, 1.2)):
        request = ScenarioRequest(
            model_version="gjr-skewt-test-fixture",
            horizon=30,
            n_paths=10,
            seed=99,
            initial_state=initial_state,
        )
        direct = simulate(fitted_artifact, request)
        reloaded = simulate(reloaded_artifact, request)
        assert direct.returns.tobytes() == reloaded.returns.tobytes()


# ---------------------------------------------------------------------------
# Proof: transport bytes may differ while identity stays stable
# ---------------------------------------------------------------------------


def test_transport_bytes_may_change_while_artifact_id_stays_identical(
    tmp_path, fitted_artifact
):
    """Rewrite artifact.json with different key order and whitespace but the
    exact same semantic values -- exactly the kind of transport-format
    accident (JSON formatter, key ordering) identity must be immune to."""
    directory = tmp_path / "artifact"
    serialization.save_artifact(fitted_artifact, directory)
    metadata_path = directory / serialization.METADATA_FILENAME
    original_bytes = metadata_path.read_bytes()

    data = json.loads(original_bytes)
    reordered = {key: data[key] for key in reversed(list(data.keys()))}
    rewritten_bytes = json.dumps(reordered, indent=4, sort_keys=False).encode("utf-8")
    assert rewritten_bytes != original_bytes  # the rewrite really did change the bytes
    metadata_path.write_bytes(rewritten_bytes)

    reloaded = serialization.load_artifact(directory)
    assert reloaded.artifact_id == fitted_artifact.artifact_id


def test_load_fails_closed_when_stored_content_and_stored_id_disagree(
    tmp_path, fitted_artifact
):
    """A tampered (or corrupted) parameter must be caught on load, not trusted."""
    directory = tmp_path / "artifact"
    serialization.save_artifact(fitted_artifact, directory)
    metadata_path = directory / serialization.METADATA_FILENAME
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    data["params"]["mu"] = data["params"]["mu"] + 1e-6
    metadata_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        serialization.load_artifact(directory)


def test_load_fails_closed_when_the_stored_id_itself_is_tampered(tmp_path, fitted_artifact):
    """Symmetric case: content is untouched but the stored id is wrong -- the
    check must fire on the id/content disagreement either direction."""
    directory = tmp_path / "artifact"
    serialization.save_artifact(fitted_artifact, directory)
    metadata_path = directory / serialization.METADATA_FILENAME
    data = json.loads(metadata_path.read_text(encoding="utf-8"))

    data["artifact_id"] = "sha256:" + "0" * 64
    metadata_path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")

    with pytest.raises(ArtifactIntegrityError):
        serialization.load_artifact(directory)


# ---------------------------------------------------------------------------
# Mutation safety: frozen dataclass + read-only arrays, not just reassignment
# ---------------------------------------------------------------------------


def test_reassigning_a_frozen_artifact_field_is_rejected(fitted_artifact):
    """``dataclass(frozen=True)`` blocks reassignment on its own -- this is
    the easy half; the harder half (in-place array mutation) is checked
    below."""
    import dataclasses

    with pytest.raises(dataclasses.FrozenInstanceError):
        fitted_artifact.artifact_id = "sha256:" + "0" * 64


def test_mutating_fitted_residuals_in_place_is_rejected(fitted_artifact):
    """``ModelArtifact.__post_init__`` copies and write-protects both fitted
    state arrays (``numpy``'s own ``WRITEABLE`` flag) specifically because
    ``dataclass(frozen=True)`` only blocks *reassigning* the field, not
    writing into the array it already points to -- an in-place write here
    would otherwise silently invalidate ``artifact_id`` without tripping any
    dataclass check. This is that claim, exercised directly rather than only
    asserted in a docstring."""
    with pytest.raises(ValueError, match="read-only"):
        fitted_artifact.fitted_residuals[0] = 999.0
    with pytest.raises(ValueError, match="read-only"):
        fitted_artifact.fitted_variances[0] = 999.0


def test_artifact_construction_defensively_copies_its_input_arrays(fitted_artifact):
    """Write-protecting the artifact's own array is not enough if it is the
    *same* array object the caller still holds a mutable reference to --
    ``__post_init__`` must copy, not merely lock the array it was handed."""
    mutable_residuals = fitted_artifact.fitted_residuals.copy()
    mutable_residuals.setflags(write=True)
    mutable_variances = fitted_artifact.fitted_variances.copy()
    mutable_variances.setflags(write=True)

    from scenario_platform.domain.artifacts import ModelArtifact

    rebuilt = ModelArtifact(
        schema_version=fitted_artifact.schema_version,
        model_version=fitted_artifact.model_version,
        artifact_id=fitted_artifact.artifact_id,
        family=fitted_artifact.family,
        params=fitted_artifact.params,
        fitted_residuals=mutable_residuals,
        fitted_variances=mutable_variances,
        diagnostics=fitted_artifact.diagnostics,
        provenance=fitted_artifact.provenance,
        threshold_set_version=fitted_artifact.threshold_set_version,
        policy_set_version=fitted_artifact.policy_set_version,
    )
    mutable_residuals[0] = -12345.0  # mutate the caller's own array after construction
    assert rebuilt.fitted_residuals[0] != -12345.0


def test_mutating_a_scenario_sets_returns_array_in_place_is_rejected(
    tmp_path, fitted_artifact, scenario_request
):
    """The same mutation-safety design applied to ``ScenarioSet`` (reports.py),
    which holds a freshly-generated array rather than a stored one."""
    scenarios = simulate(fitted_artifact, scenario_request)
    with pytest.raises(ValueError, match="read-only"):
        scenarios.returns[0, 0] = 999.0


# ---------------------------------------------------------------------------
# Negative control: a params-only reconstruction cannot reproduce historical_mix
# ---------------------------------------------------------------------------


def test_params_only_reconstruction_cannot_reproduce_historical_mix(fitted_artifact):
    """A hypothetical params-only artifact -- exactly what AGENTS.md invariant
    27 forbids ``ModelArtifact`` from being -- carries the fitted parameters
    but not the fitted (residual, variance) state pairs. Simulating from it
    with the default ``historical_mix`` initialisation does not silently
    produce a *different* (wrong) distribution: it fails outright, because
    ``historical_mix`` samples from state arrays this object never kept
    (``GjrSkewTGenerator._initial_states`` calls ``self.fitted_states``,
    which raises the moment either array is ``None``). This is the concrete
    failure mode invariant 27 exists to prevent going unnoticed.
    """
    params_only = GjrSkewTGenerator()
    params_only.params_ = fitted_artifact.params
    # fitted_residuals / fitted_variances deliberately left unset: this is
    # what a simplified, params-only artifact reconstruction looks like.

    with pytest.raises(RuntimeError, match="Call fit"):
        params_only.simulate(n_steps=10, n_paths=4, seed=1, initial_state="historical_mix")
