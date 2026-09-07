"""Section 6.2's client_request_hash: excludes every server-generated value,
includes seed only if the caller supplied one."""

from __future__ import annotations

from scenario_platform.control.hashing import client_request_hash
from scenario_platform.control.schemas import GovernanceIn, ScenarioJobIn


def _req(**overrides):
    base = dict(model_version="current", horizon=252, n_paths=1000)
    base.update(overrides)
    return ScenarioJobIn.model_validate(base)


def test_identical_requests_hash_identically():
    assert client_request_hash(_req()) == client_request_hash(_req())


def test_seed_present_vs_absent_hash_differently():
    assert client_request_hash(_req()) != client_request_hash(_req(seed=42))


def test_different_seed_values_hash_differently():
    assert client_request_hash(_req(seed=1)) != client_request_hash(_req(seed=2))


def test_hash_is_stable_regardless_of_resolved_values():
    """The hash must never take resolved_model_version / resolved_artifact_id /
    assigned_seed / job_id as input -- those do not even exist on
    ScenarioJobIn, so this is really an interface-shape guarantee, made
    explicit as a test."""
    req = _req(model_version="current")
    assert not hasattr(req, "resolved_artifact_id")
    assert not hasattr(req, "assigned_seed")
    assert not hasattr(req, "job_id")


def test_governance_participates_in_the_hash():
    plain = _req()
    with_gov = _req(governance=GovernanceIn(cap="x", approver="y").model_dump())
    assert client_request_hash(plain) != client_request_hash(with_gov)


def test_horizon_or_n_paths_change_the_hash():
    assert client_request_hash(_req(horizon=252)) != client_request_hash(_req(horizon=500))
    assert client_request_hash(_req(n_paths=1000)) != client_request_hash(
        _req(n_paths=2000)
    )


def test_hash_has_a_stable_prefix():
    assert client_request_hash(_req()).startswith("sha256:")
