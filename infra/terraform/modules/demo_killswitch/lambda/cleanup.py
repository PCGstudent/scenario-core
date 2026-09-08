"""Independent demo cleanup, installed before the watched network.

IAM time conditions on the submission and orchestration roles prohibit new
executions/placements at the deadline; task-definition deregistration is not
used as an instantaneous barrier. Endpoint Name tags and predetermined cluster
ARNs allow installation before any costly endpoint exists. Cleanup confirms
stops and deletions and reports partial failures. It never modifies data/state.
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
    return exc.response.get("Error", {}).get("Code") in (
        "InvalidVpcEndpointId.NotFound",
        "ClusterNotFoundException",
    )


def _discover_endpoints(ec2: Any) -> list[str]:
    names = _env_list("VPC_ENDPOINT_NAMES")
    if not names:
        return []
    result: list[str] = []
    for page in ec2.get_paginator("describe_vpc_endpoints").paginate(
        Filters=[
            {"Name": "tag:Name", "Values": names},
            {"Name": "vpc-endpoint-type", "Values": ["Interface"]},
        ]
    ):
        result.extend(
            endpoint["VpcEndpointId"] for endpoint in page.get("VpcEndpoints", [])
        )
    return result


# --- Step 2: stop running tasks, paginated, confirmed STOPPED --------------


def _running_task_arns(ecs: Any, cluster_arn: str, errors: list[str]) -> list[str]:
    task_arns: list[str] = []
    try:
        paginator = ecs.get_paginator("list_tasks")
        for page in paginator.paginate(cluster=cluster_arn, desiredStatus="RUNNING"):
            task_arns.extend(page.get("taskArns", []))
    except ClientError as exc:
        if not _is_not_found(exc):
            errors.append(f"list_tasks({cluster_arn}): {exc}")
    return task_arns


def _stop_and_confirm_tasks(
    ecs: Any, cluster_arns: list[str], errors: list[str]
) -> list[str]:
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
            # DescribeTasks accepts at most 100 task IDs per request.
            for offset in range(0, len(issued), 100):
                ecs.get_waiter("tasks_stopped").wait(
                    cluster=cluster_arn,
                    tasks=issued[offset : offset + 100],
                    WaiterConfig={
                        "Delay": _TASK_STOP_WAITER_DELAY_SECONDS,
                        "MaxAttempts": _TASK_STOP_WAITER_MAX_ATTEMPTS,
                    },
                )
            stopped.extend(issued)
        except (WaiterError, ClientError) as exc:
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
    to_delete: list[str] = []
    for eid in endpoint_ids:
        try:
            if _endpoint_state(ec2, eid) != "deleted":
                to_delete.append(eid)
        except ClientError as exc:
            errors.append(f"describe endpoint {eid}: {exc}")
    if not to_delete:
        return []
    try:
        resp = ec2.delete_vpc_endpoints(VpcEndpointIds=to_delete)
    except ClientError as exc:
        errors.append(f"delete_vpc_endpoints: {exc}")
        resp = {}
    for failure in resp.get("Unsuccessful", []):
        error = failure.get("Error", {})
        if error.get("Code") != "InvalidVpcEndpointId.NotFound":
            errors.append(f"delete endpoint {failure.get('ResourceId')}: {error}")
    deadline = time.monotonic() + _ENDPOINT_VERIFY_TIMEOUT_SECONDS
    remaining = set(to_delete)
    while remaining and time.monotonic() < deadline:
        for eid in list(remaining):
            try:
                if _endpoint_state(ec2, eid) == "deleted":
                    remaining.remove(eid)
            except ClientError as exc:
                errors.append(f"verify endpoint {eid}: {exc}")
                return [eid for eid in to_delete if eid not in remaining]
        if remaining:
            time.sleep(_ENDPOINT_VERIFY_POLL_SECONDS)
    if remaining:
        errors.append(f"endpoints not confirmed deleted: {sorted(remaining)}")
    return [eid for eid in to_delete if eid not in remaining]


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    ec2 = boto3.client("ec2")
    ecs = boto3.client("ecs")
    sns = boto3.client("sns")

    cluster_arns = _env_list("ECS_CLUSTER_ARNS")
    endpoint_ids = _env_list("VPC_ENDPOINT_IDS")
    topic_arn = os.environ["FAILURE_SNS_TOPIC_ARN"]
    errors: list[str] = []
    stopped_tasks: list[str] = []
    deleted_endpoints: list[str] = []
    try:
        # Two sweeps cover tasks whose placement was in flight at cutoff.
        # The role's preinstalled IAM deadline blocks subsequent RunTask.
        for _ in range(2):
            stopped_tasks.extend(_stop_and_confirm_tasks(ecs, cluster_arns, errors))
            if errors:
                break
            time.sleep(5)
        if not errors:
            endpoint_ids = sorted(set(endpoint_ids + _discover_endpoints(ec2)))
            deleted_endpoints = _delete_and_verify_endpoints(ec2, endpoint_ids, errors)
    except Exception as exc:  # noqa: BLE001 -- alert on unexpected failures too
        errors.append(f"cleanup exception: {type(exc).__name__}: {exc}")
    result = {
        "tasks_stopped_and_confirmed": sorted(set(stopped_tasks)),
        "endpoints_deleted_and_confirmed": deleted_endpoints,
        "errors": errors,
    }
    if errors:
        sns.publish(
            TopicArn=topic_arn,
            Subject="4xtra demo auto-cleanup FAILED",
            Message="Manual reconciliation required.\n" + "\n".join(errors),
        )
        raise RuntimeError(f"demo auto-cleanup failed: {'; '.join(errors)}")
    return result
