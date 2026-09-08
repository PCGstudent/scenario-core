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


# --- Interleaving/state-transition tests (job_store's conditional writes) --


def test_execution_advances_before_arn_is_persisted(approved_v1):
    """The state machine's own RecordQueued can land (status -> RUNNING)
    before this synchronous caller gets to persist execution_arn. The ARN
    must still be recorded; the status write must be a no-op, not a
    regression back to QUEUED."""
    job_store._jobs_table().put_item(
        Item={"pk": "JOB#race-1", "sk": "META", "job_id": "race-1", "status": "RUNNING"}
    )
    status = job_store.set_execution_arn(
        "race-1", "arn:aws:states:eu-west-1:123456789012:execution:x:race-1"
    )
    assert status == "RUNNING"
    job = job_store.get_job("race-1")
    assert job["status"] == "RUNNING"
    assert (
        job["execution_arn"] == "arn:aws:states:eu-west-1:123456789012:execution:x:race-1"
    )


def test_cancellation_during_startup_is_not_overwritten_and_stops_execution(approved_v1):
    """A DELETE that wins the race while the job is still SUBMITTED (no
    execution_arn yet) must stick -- the later set_execution_arn call still
    records the ARN (for provenance) but must not resurrect QUEUED, and
    submit.py's own caller (tested separately) uses the returned status to
    stop the just-started execution."""
    job_store._jobs_table().put_item(
        Item={"pk": "JOB#race-2", "sk": "META", "job_id": "race-2", "status": "SUBMITTED"}
    )
    cancelled_status = job_store.mark_cancelled("race-2")
    assert cancelled_status == "CANCELLED"

    status = job_store.set_execution_arn(
        "race-2", "arn:aws:states:eu-west-1:123456789012:execution:x:race-2"
    )
    assert status == "CANCELLED"
    job = job_store.get_job("race-2")
    assert job["status"] == "CANCELLED"
    assert (
        job["execution_arn"] == "arn:aws:states:eu-west-1:123456789012:execution:x:race-2"
    )


def test_concurrent_completion_vs_cancellation_dynamodb_condition(approved_v1):
    """Proves the exact ConditionExpression
    modules/job_orchestrator/state_machine.asl.json.tftpl's RecordSucceeded/
    RecordFailed states use ("#status <> :cancelled") behaves as intended
    against real DynamoDB semantics (via moto) -- Step Functions itself
    cannot be executed in this test, but the DynamoDB native-integration
    condition it relies on is fully testable directly."""
    from botocore.exceptions import ClientError

    table = job_store._jobs_table()
    table.put_item(
        Item={"pk": "JOB#race-3", "sk": "META", "job_id": "race-3", "status": "CANCELLED"}
    )
    with pytest.raises(ClientError) as exc_info:
        table.update_item(
            Key={"pk": "JOB#race-3"},
            UpdateExpression="SET #status = :succeeded",
            ConditionExpression="#status <> :cancelled",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={
                ":succeeded": "SUCCEEDED",
                ":cancelled": "CANCELLED",
            },
        )
    assert exc_info.value.response["Error"]["Code"] == "ConditionalCheckFailedException"
    assert job_store.get_job("race-3")["status"] == "CANCELLED"

    # The same write against a job that was NOT cancelled succeeds.
    table.put_item(
        Item={"pk": "JOB#race-4", "sk": "META", "job_id": "race-4", "status": "RUNNING"}
    )
    table.update_item(
        Key={"pk": "JOB#race-4"},
        UpdateExpression="SET #status = :succeeded",
        ConditionExpression="#status <> :cancelled",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":succeeded": "SUCCEEDED", ":cancelled": "CANCELLED"},
    )
    assert job_store.get_job("race-4")["status"] == "SUCCEEDED"


def test_replay_after_completion_returns_terminal_status_without_restarting(approved_v1):
    """A replay of an already-SUCCEEDED job must report SUCCEEDED as-is --
    healing only fires for status == SUBMITTED with no execution_arn, so a
    terminal job is never re-started."""
    job_store._jobs_table().put_item(
        Item={
            "pk": "JOB#done-1",
            "sk": "META",
            "job_id": "done-1",
            "status": "SUCCEEDED",
            "execution_arn": "arn:aws:states:eu-west-1:123456789012:execution:x:done-1",
        }
    )
    job_store._jobs_table().put_item(
        Item={
            "pk": f"IDEM#{PRINCIPAL}#done-key",
            "sk": "META",
            "job_id": "done-1",
            "client_request_hash": client_request_hash(
                ScenarioJobIn.model_validate(_body())
            ),
        }
    )
    resp = submit.handler(_event(_body(), idempotency_key="done-key"))
    assert resp["statusCode"] == 200
    out = json.loads(resp["body"])
    assert out["job_id"] == "done-1"
    assert out["status"] == "SUCCEEDED"


def test_expired_demo_rejects_start_even_during_healing(moto_env, monkeypatch):
    from scenario_platform.control import submit
    from scenario_platform.control.errors import HandlerError

    monkeypatch.setenv("DEMO_DEADLINE_UTC", "2000-01-01T00:00:00Z")
    with pytest.raises(HandlerError):
        submit._start_or_heal_execution("expired-job")
