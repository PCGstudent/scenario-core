"""Admission limits and the approval predicate (Section 6.1 steps 2e-2g, Section 8.4)."""

from __future__ import annotations

import boto3
import pytest

from scenario_platform.control import admission
from scenario_platform.control.errors import HandlerError
from scenario_platform.control.schemas import GovernanceIn, ScenarioJobIn

from .conftest import put_candidate_and_approval, put_pointer


def _req(**overrides):
    base = dict(model_version="current", horizon=252, n_paths=1000)
    base.update(overrides)
    return ScenarioJobIn.model_validate(base)


def test_admission_rejects_over_max_horizon():
    req = _req(horizon=admission.MAX_HORIZON + 1, n_paths=1)
    with pytest.raises(HandlerError) as exc_info:
        admission.check_admission(req)
    assert exc_info.value.status_code == 422


def test_admission_rejects_over_max_path_years():
    req = _req(horizon=1000, n_paths=1000)  # 1,000,000 path-years
    with pytest.raises(HandlerError):
        admission.check_admission(req)


def test_admission_accepts_within_limits():
    admission.check_admission(_req(horizon=252, n_paths=1000))  # must not raise


def test_policy_rejects_restricted_level_without_governance():
    req = _req(metrics=[0.999])
    with pytest.raises(HandlerError) as exc_info:
        admission.check_policy(req)
    assert exc_info.value.status_code == 422


def test_policy_accepts_restricted_level_with_governance():
    req = _req(metrics=[0.999], governance=GovernanceIn(cap="x", approver="y"))
    admission.check_policy(req)  # must not raise


def test_policy_accepts_validated_levels():
    admission.check_policy(_req(metrics=[0.95, 0.99]))  # must not raise


def test_resolve_current_rejects_when_no_pointer_exists(moto_env):
    with pytest.raises(HandlerError) as exc_info:
        admission.resolve_and_authorize("current")
    assert exc_info.value.error == "NoCurrentPointer"


def test_resolve_named_version_rejects_when_no_candidate_or_approval_exists(moto_env):
    with pytest.raises(HandlerError) as exc_info:
        admission.resolve_and_authorize("gjr-skewt-unknown")
    assert exc_info.value.error == "NotApproved"


def test_resolve_rejects_when_approval_names_a_different_artifact(moto_env):
    registry = boto3.resource("dynamodb", region_name=moto_env["region"]).Table(
        moto_env["registry_table"]
    )
    registry.put_item(
        Item={
            "pk": f"CANDIDATE#{admission.FAMILY}#v1",
            "sk": "META",
            "artifact_id": "sha256:aaa",
        }
    )
    registry.put_item(
        Item={
            "pk": f"APPROVAL#{admission.FAMILY}#v1",
            "sk": "META",
            "artifact_id": "sha256:bbb",  # disagrees with the candidate
        }
    )
    with pytest.raises(HandlerError) as exc_info:
        admission.resolve_and_authorize("v1")
    assert exc_info.value.error == "NotApproved"


def test_resolve_succeeds_for_a_matching_candidate_and_approval(moto_env):
    registry = boto3.resource("dynamodb", region_name=moto_env["region"]).Table(
        moto_env["registry_table"]
    )
    put_candidate_and_approval(
        registry, family=admission.FAMILY, model_version="v1", artifact_id="sha256:aaa"
    )
    version, artifact_id = admission.resolve_and_authorize("v1")
    assert version == "v1"
    assert artifact_id == "sha256:aaa"


def test_resolve_current_follows_the_pointer(moto_env):
    registry = boto3.resource("dynamodb", region_name=moto_env["region"]).Table(
        moto_env["registry_table"]
    )
    put_candidate_and_approval(
        registry, family=admission.FAMILY, model_version="v2", artifact_id="sha256:ccc"
    )
    put_pointer(
        registry, family=admission.FAMILY, model_version="v2", artifact_id="sha256:ccc"
    )
    version, artifact_id = admission.resolve_and_authorize("current")
    assert version == "v2"
    assert artifact_id == "sha256:ccc"


def test_resolve_current_rejects_pointer_artifact_mismatch(moto_env):
    registry = boto3.resource("dynamodb", region_name=moto_env["region"]).Table(
        moto_env["registry_table"]
    )
    put_candidate_and_approval(
        registry, family=admission.FAMILY, model_version="v3", artifact_id="sha256:ddd"
    )
    put_pointer(
        registry, family=admission.FAMILY, model_version="v3", artifact_id="sha256:WRONG"
    )
    with pytest.raises(HandlerError) as exc_info:
        admission.resolve_and_authorize("current")
    assert exc_info.value.error == "PointerInconsistent"
