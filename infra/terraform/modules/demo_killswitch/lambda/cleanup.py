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

**Order of operations, and why each step is where it is:**

1. **Deregister the watched task definition(s) first.** This is what
   actually stops NEW compute from being placed while cleanup is in
   progress: Step Functions' `RunSimulation` state references one fixed
   task-definition ARN, baked in at `terraform apply` time, so
   deregistering that exact revision makes any subsequent `ecs:RunTask`
   call against it fail immediately at the ECS API level -- a submission
   that arrives mid-cleanup cannot place a task, regardless of how many
   times the state machine's own bounded retry loop tries. (A resulting
   `RunTask` API failure is classified `TRANSIENT_INFRA` by
   `classify_failure.py`'s existing "unrecognised ECS error" branch, so
   such a submission still exhausts its retry budget before failing
   closed -- this stops compute from being PLACED, not from being
   *retried at the Step Functions level*, which is a real but bounded and
   disclosed limitation.) Deregistering does not affect tasks already
   running.
2. **Stop already-running Fargate tasks**, paginated, with STOPPED
   confirmed via a waiter -- not fire-and-forget. A running task depends
   on the endpoints for DynamoDB/S3/ECR/Logs access, so deleting them out
   from under it does not stop that task's own billed compute; only a
   confirmed `StopTask` does.
3. **Delete the interface endpoints**, verified individually (never as one
   batched call whose failure on one ID would mask the state of the
   others) and polled until each genuinely reports `deleted` (not
   `deleting`, which is still in progress, not yet done).

**Idempotent.** Every step describes current state before acting and
treats "already gone" as success, not as an error to retry into. Safe to
invoke more than once.

**Verification, not assumption**, at every step -- deregistration is
confirmed by describing the task definition afterward; task stops are
confirmed by a waiter, not by the `StopTask` call merely being accepted;
endpoint deletion is confirmed by polling each id individually until it
reports `deleted`.

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
from botocore.exceptions import ClientError, WaiterError

_ENDPOINT_VERIFY_TIMEOUT_SECONDS = 90
_ENDPOINT_VERIFY_POLL_SECONDS = 5
_TASK_STOP_WAITER_DELAY_SECONDS = 6
_TASK_STOP_WAITER_MAX_ATTEMPTS = 15  # ~90s total


def _env_list(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _is_not_found(exc: ClientError) -> bool:
    code = exc.response.get("Error", {}).get("Code", "")
    return code in (
        "InvalidVpcEndpointId.NotFound",
        "ClientException",  # ECS's own "task definition does not exist" style code
    ) or "does not exist" in str(exc)


# --- Step 1: deregister the watched task definition(s) ---------------------


def _deregister_task_definitions(
    ecs: Any, task_definition_arns: list[str], errors: list[str]
) -> list[str]:
    deregistered: list[str] = []
    for arn in task_definition_arns:
        try:
            resp = ecs.describe_task_definition(taskDefinition=arn)
            status = resp["taskDefinition"].get("status")
        except ClientError as exc:
            if _is_not_found(exc):
                continue  # already gone -- idempotent, nothing to do
            errors.append(f"describe_task_definition({arn}): {exc}")
            continue

        if status == "INACTIVE":
            continue  # already deregistered by an earlier/retried invocation

        try:
            ecs.deregister_task_definition(taskDefinition=arn)
            deregistered.append(arn)
        except ClientError as exc:
            if _is_not_found(exc):
                continue
            errors.append(f"deregister_task_definition({arn}): {exc}")
            continue

        # Verify, not assume: confirm the revision actually reports INACTIVE
        # before treating this step as done.
        try:
            confirm = ecs.describe_task_definition(taskDefinition=arn)
            if confirm["taskDefinition"].get("status") != "INACTIVE":
                errors.append(f"deregister_task_definition({arn}): still ACTIVE after call")
        except ClientError as exc:
            errors.append(f"describe_task_definition (verify) ({arn}): {exc}")

    return deregistered


# --- Step 2: stop running tasks, paginated, confirmed STOPPED --------------


def _running_task_arns(ecs: Any, cluster_arn: str, errors: list[str]) -> list[str]:
    task_arns: list[str] = []
    try:
        paginator = ecs.get_paginator("list_tasks")
        for page in paginator.paginate(cluster=cluster_arn, desiredStatus="RUNNING"):
            task_arns.extend(page.get("taskArns", []))
    except ClientError as exc:
        errors.append(f"list_tasks({cluster_arn}): {exc}")
    return task_arns


def _stop_and_confirm_tasks(ecs: Any, cluster_arns: list[str], errors: list[str]) -> list[str]:
    stopped: list[str] = []
    for cluster_arn in cluster_arns:
        task_arns = _running_task_arns(ecs, cluster_arn, errors)
        if not task_arns:
            continue

        issued: list[str] = []
        for task_arn in task_arns:
            try:
                ecs.stop_task(
                    cluster=cluster_arn,
                    task=task_arn,
                    reason="4xtra demo auto-cleanup: window deadline reached",
                )
                issued.append(task_arn)
            except ClientError as exc:
                if _is_not_found(exc):
                    continue
                errors.append(f"stop_task({task_arn}): {exc}")

        if not issued:
            continue

        try:
            ecs.get_waiter("tasks_stopped").wait(
                cluster=cluster_arn,
                tasks=issued,
                WaiterConfig={
                    "Delay": _TASK_STOP_WAITER_DELAY_SECONDS,
                    "MaxAttempts": _TASK_STOP_WAITER_MAX_ATTEMPTS,
                },
            )
            stopped.extend(issued)
        except WaiterError as exc:
            errors.append(
                f"tasks in {cluster_arn} not confirmed STOPPED within "
                f"{_TASK_STOP_WAITER_DELAY_SECONDS * _TASK_STOP_WAITER_MAX_ATTEMPTS}s: "
                f"{issued} ({exc})"
            )
    return stopped


# --- Step 3: delete interface endpoints, verified individually -------------


def _endpoint_state(ec2: Any, endpoint_id: str) -> str:
    """``"deleted"`` (including "never existed"), or the endpoint's real
    current state (``"available"``, ``"pending"``, ``"deleting"``, ...).
    Queried one id at a time -- a batched ``DescribeVpcEndpoints`` call
    fails ENTIRELY if any single id in the batch is unrecognised, which
    would otherwise mask the real state of every other id in the same
    call."""
    try:
        resp = ec2.describe_vpc_endpoints(VpcEndpointIds=[endpoint_id])
    except ClientError as exc:
        if _is_not_found(exc):
            return "deleted"
        raise
    endpoints = resp.get("VpcEndpoints", [])
    if not endpoints:
        return "deleted"
    state = endpoints[0].get("State", "")
    # "deleting" is IN PROGRESS, not done -- only "deleted" counts as the
    # terminal, billing-has-stopped state. An earlier version of this
    # function treated "deleting" as already-removed, which could report
    # success (and stop polling) while the endpoint -- and its hourly
    # charge -- was still technically present.
    return "deleted" if state == "deleted" else state


def _delete_and_verify_endpoints(
    ec2: Any, endpoint_ids: list[str], errors: list[str]
) -> list[str]:
    to_delete = [eid for eid in endpoint_ids if _endpoint_state(ec2, eid) != "deleted"]
    if not to_delete:
        return []

    try:
        resp = ec2.delete_vpc_endpoints(VpcEndpointIds=to_delete)
    except ClientError as exc:
        errors.append(f"delete_vpc_endpoints({to_delete}): {exc}")
        resp = {}

    for failure in resp.get("Unsuccessful", []):
        error = failure.get("Error", {})
        if error.get("Code") in ("InvalidVpcEndpointId.NotFound",):
            continue  # already gone -- not a real failure
        errors.append(
            f"delete_vpc_endpoints({failure.get('VpcEndpointId')}): "
            f"{error.get('Code')}: {error.get('Message')}"
        )

    deadline = time.time() + _ENDPOINT_VERIFY_TIMEOUT_SECONDS
    remaining = set(to_delete)
    while remaining and time.time() < deadline:
        remaining = {eid for eid in remaining if _endpoint_state(ec2, eid) != "deleted"}
        if remaining:
            time.sleep(_ENDPOINT_VERIFY_POLL_SECONDS)

    if remaining:
        errors.append(
            f"endpoints not confirmed deleted within {_ENDPOINT_VERIFY_TIMEOUT_SECONDS}s: "
            f"{sorted(remaining)}"
        )
    return [eid for eid in to_delete if eid not in remaining]


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    ec2 = boto3.client("ec2")
    ecs = boto3.client("ecs")
    sns = boto3.client("sns")

    cluster_arns = _env_list("ECS_CLUSTER_ARNS")
    endpoint_ids = _env_list("VPC_ENDPOINT_IDS")
    task_definition_arns = _env_list("TASK_DEFINITION_ARNS")
    topic_arn = os.environ["FAILURE_SNS_TOPIC_ARN"]

    errors: list[str] = []
    deregistered = _deregister_task_definitions(ecs, task_definition_arns, errors)
    stopped_tasks = _stop_and_confirm_tasks(ecs, cluster_arns, errors)
    deleted_endpoints = _delete_and_verify_endpoints(ec2, endpoint_ids, errors)

    result = {
        "task_definitions_deregistered": deregistered,
        "tasks_stopped_and_confirmed": stopped_tasks,
        "endpoints_deleted_and_confirmed": deleted_endpoints,
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
