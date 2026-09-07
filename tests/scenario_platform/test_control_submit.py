"""POST /scenario-jobs: idempotency (Section 6.2), healing (Section 6.2a),
admission/policy rejections, authn (Section 6.1 step 2)."""

from __future__ import annotations

import json

import boto3
import pytest

from scenario_platform.adapters import job_store
from scenario_platform.control import admission, submit
from scenario_platform.control.hashing import client_request_hash
from scenario_platform.control.schemas import ScenarioJobIn

from .conftest import put_candidate_and_approval, put_pointer

PRINCIPAL = "arn:aws:iam::123456789012:user/test"


def _event(body: dict, *, idempotency_key: str | None = None) -> dict:
    event = {
        "requestContext": {"authorizer": {"iam": {"userArn": PRINCIPAL}}},
        "headers": {},
        "body": json.dumps(body),
    }
    if idempotency_key is not None:
        event["headers"]["idempotency-key"] = idempotency_key
    return event


@pytest.fixture
def approved_v1(moto_env):
    registry = boto3.resource("dynamodb", region_name=moto_env["region"]).Table(
        moto_env["registry_table"]
    )
    put_candidate_and_approval(
        registry, family=admission.FAMILY, model_version="v1", artifact_id="sha256:aaa"
    )
    return moto_env


def _body(**overrides):
    base = dict(model_version="v1", horizon=252, n_paths=1000, seed=42)
    base.update(overrides)
    return base


def test_new_submission_returns_202_and_creates_a_queued_job(approved_v1):
    resp = submit.handler(_event(_body()))
    assert resp["statusCode"] == 202
    out = json.loads(resp["body"])
    assert out["status"] == "QUEUED"
    job = job_store.get_job(out["job_id"])
    assert job["status"] == "QUEUED"
    assert "execution_arn" in job
    assert job["request"]["artifact_id"] == "sha256:aaa"
    assert job["request"]["seed"] == 42


def test_two_submissions_without_idempotency_key_create_two_jobs(approved_v1):
    first = json.loads(submit.handler(_event(_body()))["body"])
    second = json.loads(submit.handler(_event(_body()))["body"])
    assert first["job_id"] != second["job_id"]


def test_seed_is_assigned_when_absent(approved_v1):
    resp = submit.handler(_event(_body(seed=None)))
    out = json.loads(resp["body"])
    job = job_store.get_job(out["job_id"])
    # get_job returns DynamoDB's own Decimal for every number (job_store.
    # to_native's docstring) -- int(...) here mirrors what a real caller
    # (worker/__main__.py's job-id path, control/jobs.py's GET handler)
    # does via to_native before using the value.
    assert int(job["request"]["seed"]) >= 0


def test_replay_with_matching_hash_returns_200_and_same_job_id(approved_v1):
    first = json.loads(submit.handler(_event(_body(), idempotency_key="k1"))["body"])
    resp2 = submit.handler(_event(_body(), idempotency_key="k1"))
    assert resp2["statusCode"] == 200
    second = json.loads(resp2["body"])
    assert second["job_id"] == first["job_id"]


def test_replay_with_different_request_returns_409(approved_v1):
    submit.handler(_event(_body(horizon=252), idempotency_key="k2"))
    resp2 = submit.handler(_event(_body(horizon=500), idempotency_key="k2"))
    assert resp2["statusCode"] == 409


def test_idempotent_replay_survives_pointer_move(approved_v1):
    """Section 24 Phase 3b tests: 'the same key still replays correctly after
    POINTER#{family} is moved to a different approved version, returning the
    original resolved_artifact_id and assigned_seed'."""
    registry = boto3.resource("dynamodb", region_name="eu-west-1").Table(
        approved_v1["registry_table"]
    )
    put_candidate_and_approval(
        registry, family=admission.FAMILY, model_version="v2", artifact_id="sha256:bbb"
    )
    put_pointer(
        registry, family=admission.FAMILY, model_version="v1", artifact_id="sha256:aaa"
    )

    body = _body(model_version="current", seed=None)
    first = json.loads(submit.handler(_event(body, idempotency_key="k3"))["body"])
    first_job = job_store.get_job(first["job_id"])
    original_seed = first_job["request"]["seed"]
    original_artifact = first_job["request"]["artifact_id"]
    assert original_artifact == "sha256:aaa"

    # Move the pointer.
    put_pointer(
        registry, family=admission.FAMILY, model_version="v2", artifact_id="sha256:bbb"
    )

    replay = json.loads(submit.handler(_event(body, idempotency_key="k3"))["body"])
    assert replay["job_id"] == first["job_id"]
    replay_job = job_store.get_job(replay["job_id"])
    assert replay_job["request"]["artifact_id"] == original_artifact
    assert replay_job["request"]["seed"] == original_seed


def test_healing_starts_a_missing_execution_on_replay(approved_v1):
    """A JOB# item stuck at SUBMITTED with no execution_arn (Section 6.2a's
    'no orphan window' healing invariant) is started by the next replay."""
    body = _body()
    new_job = job_store.NewJob(
        job_id="stuck-job-1",
        canonical_request={
            "model_version": "v1",
            "artifact_id": "sha256:aaa",
            "horizon": 252,
            "n_paths": 1000,
            "seed": 42,
            "initial_state": "historical_mix",
            "rng_scheme": "single",
            "return_variance": False,
            "risk_levels": [0.95, 0.99],
            "governance": None,
        },
        provenance={},
    )
    job_store.submit_job(
        new_job,
        principal=PRINCIPAL,
        idempotency_key="stuck-key",
        client_request_hash=client_request_hash(ScenarioJobIn.model_validate(body)),
        assigned_seed=42,
        resolved_model_version="v1",
        resolved_artifact_id="sha256:aaa",
    )
    job_before = job_store.get_job("stuck-job-1")
    assert job_before["status"] == "SUBMITTED"
    assert "execution_arn" not in job_before

    resp = submit.handler(_event(body, idempotency_key="stuck-key"))
    assert resp["statusCode"] == 200
    out = json.loads(resp["body"])
    assert out["job_id"] == "stuck-job-1"
    assert out["status"] == "QUEUED"
    healed = job_store.get_job("stuck-job-1")
    assert "execution_arn" in healed


def test_admission_rejection_returns_422(approved_v1):
    resp = submit.handler(_event(_body(horizon=999999999)))
    assert resp["statusCode"] == 422


def test_policy_rejection_returns_422(approved_v1):
    resp = submit.handler(_event(_body(metrics=[0.999])))
    assert resp["statusCode"] == 422


def test_unapproved_model_version_returns_422(moto_env):
    resp = submit.handler(_event(_body(model_version="nope")))
    assert resp["statusCode"] == 422


def test_missing_authorizer_context_returns_401(moto_env):
    event = {"headers": {}, "body": json.dumps(_body())}
    resp = submit.handler(event)
    assert resp["statusCode"] == 401


def test_malformed_json_returns_422(moto_env):
    event = {
        "requestContext": {"authorizer": {"iam": {"userArn": PRINCIPAL}}},
        "headers": {},
        "body": "{not json",
    }
    resp = submit.handler(event)
    assert resp["statusCode"] == 422


def test_unknown_field_returns_422(approved_v1):
    resp = submit.handler(_event(_body(bogus_field=True)))
    assert resp["statusCode"] == 422
