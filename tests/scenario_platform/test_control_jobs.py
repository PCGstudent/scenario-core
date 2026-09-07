"""GET status, GET results, DELETE cancel (Section 6.1 steps 5-7)."""

from __future__ import annotations

import json

import boto3

from scenario_platform.adapters import job_store
from scenario_platform.control import jobs

PRINCIPAL = "arn:aws:iam::123456789012:user/test"


def _event(*, job_id: str, method: str, results: bool = False) -> dict:
    path = f"/scenario-jobs/{job_id}" + ("/results" if results else "")
    return {
        "requestContext": {
            "authorizer": {"iam": {"userArn": PRINCIPAL}},
            "http": {"method": method},
        },
        "pathParameters": {"job_id": job_id},
        "rawPath": path,
    }


def _make_job(moto_env, job_id: str, status: str, **extra):
    job_store._jobs_table().put_item(
        Item={
            "pk": f"JOB#{job_id}",
            "sk": "META",
            "job_id": job_id,
            "status": status,
            **extra,
        }
    )


def test_get_status_404_for_unknown_job(moto_env):
    resp = jobs.handler(_event(job_id="nope", method="GET"))
    assert resp["statusCode"] == 404


def test_get_status_returns_job_fields(moto_env):
    _make_job(moto_env, "j1", "RUNNING", request={"horizon": 252})
    resp = jobs.handler(_event(job_id="j1", method="GET"))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["status"] == "RUNNING"
    assert body["request"]["horizon"] == 252


def test_get_results_409_when_not_succeeded(moto_env):
    _make_job(moto_env, "j2", "RUNNING")
    resp = jobs.handler(_event(job_id="j2", method="GET", results=True))
    assert resp["statusCode"] == 409


def test_get_results_500_when_succeeded_but_manifest_missing(moto_env):
    _make_job(moto_env, "j3", "SUCCEEDED")
    resp = jobs.handler(_event(job_id="j3", method="GET", results=True))
    assert resp["statusCode"] == 500


def test_get_results_returns_manifest_and_presigned_url(moto_env):
    _make_job(moto_env, "j4", "SUCCEEDED")
    s3 = boto3.client("s3", region_name=moto_env["region"])
    s3.put_object(
        Bucket=moto_env["runs_bucket"],
        Key="runs/j4/manifest.json",
        Body=json.dumps({"artifact_id": "sha256:aaa"}),
    )
    s3.put_object(
        Bucket=moto_env["runs_bucket"],
        Key="runs/j4/risk_report.json",
        Body=json.dumps({"var_es": {}}),
    )
    resp = jobs.handler(_event(job_id="j4", method="GET", results=True))
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["manifest"]["artifact_id"] == "sha256:aaa"
    assert "returns_url" in body


def test_delete_marks_cancelled_when_no_execution_started(moto_env):
    _make_job(moto_env, "j5", "SUBMITTED")
    resp = jobs.handler(_event(job_id="j5", method="DELETE"))
    assert resp["statusCode"] == 200
    assert job_store.get_job("j5")["status"] == "CANCELLED"


def test_delete_is_a_noop_on_a_terminal_job(moto_env):
    _make_job(moto_env, "j6", "SUCCEEDED")
    resp = jobs.handler(_event(job_id="j6", method="DELETE"))
    assert resp["statusCode"] == 200
    assert job_store.get_job("j6")["status"] == "SUCCEEDED"


def test_missing_job_id_path_param_returns_400(moto_env):
    event = {
        "requestContext": {
            "authorizer": {"iam": {"userArn": PRINCIPAL}},
            "http": {"method": "GET"},
        },
        "pathParameters": {},
        "rawPath": "/scenario-jobs/",
    }
    resp = jobs.handler(event)
    assert resp["statusCode"] == 400
