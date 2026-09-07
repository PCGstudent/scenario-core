"""Shared moto fixtures for Phase 3b control-plane / adapter tests.

Every fixture here mocks AWS in-process (moto's ``mock_aws``) -- no network
access, no real AWS account touched, consistent with this repository's
existing rule that the test suite never depends on network access.
"""

from __future__ import annotations

import json

import boto3
import pytest
from moto import mock_aws

JOBS_TABLE = "test-scenario-jobs"
REGISTRY_TABLE = "test-model-registry"
ARTIFACTS_BUCKET = "test-artifacts-bucket"
RUNS_BUCKET = "test-runs-bucket"
REGION = "eu-west-1"


@pytest.fixture
def aws_credentials(monkeypatch):
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "testing")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "testing")
    monkeypatch.setenv("AWS_SECURITY_TOKEN", "testing")
    monkeypatch.setenv("AWS_SESSION_TOKEN", "testing")
    monkeypatch.setenv("AWS_DEFAULT_REGION", REGION)


def _reset_cached_aws_clients():
    """Every adapter/control module caches its boto3 client/resource at
    module level (a real, deliberate optimization for a warm Lambda --
    docs in job_store.py/s3_store.py's own ``_resource()``/``_client()``
    helpers). That caching is exactly what breaks test isolation under
    moto: a client built inside one test's ``mock_aws()`` context stays
    wired to that context's now-torn-down mock state once the context
    exits, so the NEXT test's calls through the same cached client fail in
    confusing ways (observed directly: identical item content that
    succeeds against a freshly-created client fails with a moto-internal
    ``TransactionCanceledException`` when routed through a client cached
    from an earlier, already-exited ``mock_aws()`` block). Resetting every
    cache before each test re-creates the client inside the CURRENT
    ``mock_aws()`` context on first use.
    """
    import scenario_platform.adapters.job_store as job_store_module
    import scenario_platform.adapters.s3_store as s3_store_module
    import scenario_platform.control.classify_failure as classify_failure_module
    import scenario_platform.control.jobs as jobs_module
    import scenario_platform.control.submit as submit_module

    job_store_module._dynamodb_resource = None
    job_store_module._dynamodb_client = None
    s3_store_module._s3_client = None
    submit_module._sfn_client = None
    jobs_module._sfn_client = None
    classify_failure_module._ecs_client = None


@pytest.fixture
def moto_env(aws_credentials, monkeypatch):
    _reset_cached_aws_clients()
    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name=REGION)
        dynamodb.create_table(
            TableName=JOBS_TABLE,
            AttributeDefinitions=[{"AttributeName": "pk", "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            BillingMode="PAY_PER_REQUEST",
        )
        dynamodb.create_table(
            TableName=REGISTRY_TABLE,
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "sk", "AttributeType": "S"},
            ],
            KeySchema=[
                {"AttributeName": "pk", "KeyType": "HASH"},
                {"AttributeName": "sk", "KeyType": "RANGE"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        s3 = boto3.client("s3", region_name=REGION)
        s3.create_bucket(
            Bucket=ARTIFACTS_BUCKET,
            CreateBucketConfiguration={"LocationConstraint": REGION},
        )
        s3.create_bucket(
            Bucket=RUNS_BUCKET, CreateBucketConfiguration={"LocationConstraint": REGION}
        )

        sfn = boto3.client("stepfunctions", region_name=REGION)
        sfn_role_arn = "arn:aws:iam::123456789012:role/test-sfn-role"
        state_machine_arn = sfn.create_state_machine(
            name="test-scenario-job",
            definition=json.dumps(
                {"StartAt": "Noop", "States": {"Noop": {"Type": "Pass", "End": True}}}
            ),
            roleArn=sfn_role_arn,
        )["stateMachineArn"]

        monkeypatch.setenv("SCENARIO_JOBS_TABLE", JOBS_TABLE)
        monkeypatch.setenv("MODEL_REGISTRY_TABLE", REGISTRY_TABLE)
        monkeypatch.setenv("ARTIFACTS_BUCKET", ARTIFACTS_BUCKET)
        monkeypatch.setenv("RUNS_BUCKET", RUNS_BUCKET)
        monkeypatch.setenv("STATE_MACHINE_ARN", state_machine_arn)

        yield {
            "jobs_table": JOBS_TABLE,
            "registry_table": REGISTRY_TABLE,
            "artifacts_bucket": ARTIFACTS_BUCKET,
            "runs_bucket": RUNS_BUCKET,
            "state_machine_arn": state_machine_arn,
            "region": REGION,
        }

    _reset_cached_aws_clients()


def put_candidate_and_approval(
    registry_table_resource,
    *,
    family,
    model_version,
    artifact_id,
    approved_by="test-approver",
):
    from datetime import UTC, datetime

    now = datetime.now(UTC).isoformat()
    registry_table_resource.put_item(
        Item={
            "pk": f"CANDIDATE#{family}#{model_version}",
            "sk": "META",
            "artifact_id": artifact_id,
            "created_at": now,
        }
    )
    registry_table_resource.put_item(
        Item={
            "pk": f"APPROVAL#{family}#{model_version}",
            "sk": "META",
            "artifact_id": artifact_id,
            "approved_by": approved_by,
            "approved_at": now,
        }
    )


def put_pointer(registry_table_resource, *, family, model_version, artifact_id):
    registry_table_resource.put_item(
        Item={
            "pk": f"POINTER#{family}",
            "sk": "CURRENT",
            "model_version": model_version,
            "artifact_id": artifact_id,
        }
    )
