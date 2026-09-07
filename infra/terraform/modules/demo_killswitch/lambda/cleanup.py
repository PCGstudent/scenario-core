"""The demo auto-cleanup Lambda -- an AWS-native dead-man's switch, deliberately
independent of GitHub Actions (its runs are currently failing at startup,
per this project's own standing instruction not to rely on it for this) and
of any local machine (a laptop being asleep, closed or offline must not
prevent this from firing).

Triggered by a ONE-TIME `aws_scheduler_schedule` (EventBridge Scheduler),
created alongside the demo's costly resources so it exists for exactly as
long as they do and no longer. Deliberately narrow: this is a single-purpose
kill switch for the one dominant demo-window cost line (the VPC interface
endpoints) plus whatever Fargate compute might still be running against
them -- it never runs `terraform destroy`, never touches S3/DynamoDB/KMS,
and its IAM role (see ../main.tf) grants nothing beyond what this file
actually calls.

**Idempotent.** Safe to invoke more than once (a retry, or a manual
re-invoke after a partial failure): every step describes current state
before acting and skips anything already gone.

**Order matters.** Fargate tasks are stopped BEFORE the endpoints are
deleted, not after -- a running task depends on the endpoints for
DynamoDB/S3/ECR/Logs access, and deleting them out from under a live task
does not stop that task's own compute billing; only `StopTask` does. This
directly answers the concern that "deleting endpoints alone does not
guarantee compute stops billing."

**Verification, not assumption.** After issuing the delete calls, this
polls `DescribeVpcEndpoints` until every targeted endpoint reports
`deleted`/`deleting` or a bounded timeout elapses. A timeout is treated as
failure, not silently swallowed.

**On any failure**, this publishes to the operator SNS topic (never
silently exits non-zero into a log nobody reads) and then re-raises, so the
Lambda's own `AWS/Lambda Errors` metric also fires -- the CloudWatch alarm
in ../main.tf is a backstop for the case where this function fails too
early or too badly to reach the `sns.publish` call itself.
"""

from __future__ import annotations

import os
import time
from typing import Any

import boto3
from botocore.exceptions import ClientError

_VERIFY_TIMEOUT_SECONDS = 60
_VERIFY_POLL_SECONDS = 5
_TERMINAL_ENDPOINT_STATES = ("deleted", "deleting")


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _stop_running_tasks(ecs: Any, cluster_arns: list[str], errors: list[str]) -> None:
    for cluster_arn in cluster_arns:
        try:
            task_arns = ecs.list_tasks(cluster=cluster_arn, desiredStatus="RUNNING").get(
                "taskArns", []
            )
        except ClientError as exc:
            errors.append(f"list_tasks({cluster_arn}): {exc}")
            continue
        for task_arn in task_arns:
            try:
                ecs.stop_task(
                    cluster=cluster_arn,
                    task=task_arn,
                    reason="4xtra demo auto-cleanup: window deadline reached",
                )
            except ClientError as exc:
                errors.append(f"stop_task({task_arn}): {exc}")


def _existing_endpoints(ec2: Any, endpoint_ids: list[str]) -> set[str]:
    if not endpoint_ids:
        return set()
    try:
        resp = ec2.describe_vpc_endpoints(VpcEndpointIds=endpoint_ids)
    except ClientError as exc:
        if "InvalidVpcEndpointId.NotFound" in str(exc):
            return set()
        raise
    return {
        e["VpcEndpointId"]
        for e in resp.get("VpcEndpoints", [])
        if e.get("State") not in _TERMINAL_ENDPOINT_STATES
    }


def _delete_and_verify_endpoints(ec2: Any, endpoint_ids: list[str], errors: list[str]) -> list[str]:
    try:
        remaining = _existing_endpoints(ec2, endpoint_ids)
    except ClientError as exc:
        errors.append(f"describe_vpc_endpoints: {exc}")
        return []

    deleted_now = sorted(remaining)
    if remaining:
        try:
            ec2.delete_vpc_endpoints(VpcEndpointIds=sorted(remaining))
        except ClientError as exc:
            errors.append(f"delete_vpc_endpoints({sorted(remaining)}): {exc}")

    deadline = time.time() + _VERIFY_TIMEOUT_SECONDS
    while remaining and time.time() < deadline:
        time.sleep(_VERIFY_POLL_SECONDS)
        try:
            remaining = _existing_endpoints(ec2, sorted(remaining))
        except ClientError as exc:
            errors.append(f"describe_vpc_endpoints (verify): {exc}")
            break

    if remaining:
        errors.append(
            f"endpoints not confirmed removed within {_VERIFY_TIMEOUT_SECONDS}s: {sorted(remaining)}"
        )
    return deleted_now


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    ec2 = boto3.client("ec2")
    ecs = boto3.client("ecs")
    sns = boto3.client("sns")

    cluster_arns = _env_list("ECS_CLUSTER_ARNS")
    endpoint_ids = _env_list("VPC_ENDPOINT_IDS")
    topic_arn = os.environ["FAILURE_SNS_TOPIC_ARN"]

    errors: list[str] = []
    _stop_running_tasks(ecs, cluster_arns, errors)
    deleted_now = _delete_and_verify_endpoints(ec2, endpoint_ids, errors)

    result = {
        "stopped_task_check_clusters": cluster_arns,
        "endpoints_targeted": endpoint_ids,
        "endpoints_deleted_this_invocation": deleted_now,
        "errors": errors,
    }

    if errors:
        sns.publish(
            TopicArn=topic_arn,
            Subject="4xtra demo auto-cleanup FAILED",
            Message=(
                "The scheduled demo auto-cleanup Lambda encountered errors and could "
                "not confirm every targeted resource was removed. Manual teardown is "
                "required now -- see docs/aws-demo-runbook.md's reconciliation "
                f"procedure.\n\nDetails:\n{chr(10).join(errors)}"
            ),
        )
        raise RuntimeError(f"demo auto-cleanup failed: {'; '.join(errors)}")

    return result
