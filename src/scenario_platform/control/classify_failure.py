"""``ClassifyFailure``: the Step Functions Task Lambda behind ``Catch: States.ALL``
(architecture plan Section 6.3).

**Input contract** (set by the state machine's ``Parameters``/``ResultPath``,
``infra/terraform/modules/job_orchestrator``'s ``state_machine.asl.json`` --
these field names are this project's own choice, not an AWS-defined shape):

```json
{"job_id": "...", "attempt": 1, "cluster_arn": "arn:aws:ecs:...:cluster/...",
 "error_info": {"Error": "States.TaskFailed", "Cause": "..."}}
```

**Output**: ``{error_class, stop_code, stopped_reason, exit_code, attempt}``,
consumed by the state machine's ``RetryDecision`` ``Choice`` state (never a
declarative ``Retry`` block -- Section 6.3 explains why that cannot express
this classification).

Classification table (Section 6.3's own table, reproduced in code, not
reinvented): a non-zero *known* container exit code is always terminal
(deterministic; re-running reproduces it); ECS placement/API faults and a
container's first image-pull failure are ``TRANSIENT_INFRA`` (bounded
retry); a second consecutive pull failure is ``CONFIG``; OOM and
``States.Timeout`` are terminal with their own named classes; anything this
table cannot place is ``UNCLASSIFIED`` and is never retried -- "unknown
failures fail closed."
"""

from __future__ import annotations

import json
from typing import Any

import boto3

from scenario_platform.worker.errors import ERROR_CLASS_BY_EXIT_CODE

from ..adapters.logging import configure_logging

LOGGER = configure_logging("scenario_platform.control.classify_failure")

_ecs_client: Any = None


def _ecs() -> Any:
    global _ecs_client
    if _ecs_client is None:
        _ecs_client = boto3.client("ecs")
    return _ecs_client


def _try_parse_cause(cause: str | None) -> dict[str, Any]:
    if not cause:
        return {}
    try:
        parsed = json.loads(cause)
        return parsed if isinstance(parsed, dict) else {}
    except json.JSONDecodeError:
        return {}


def _extract_task_arn(cause_parsed: dict[str, Any]) -> str | None:
    # The optimized ecs:runTask.sync integration's failure Cause is
    # documented to vary in shape; a task ARN, when present, shows up under
    # one of these keys depending on which failure path produced it.
    for key in ("TaskArn", "taskArn"):
        if key in cause_parsed:
            return str(cause_parsed[key])
    tasks = cause_parsed.get("Tasks") or cause_parsed.get("tasks")
    if isinstance(tasks, list) and tasks:
        arn = tasks[0].get("TaskArn") or tasks[0].get("taskArn")
        if arn:
            return str(arn)
    return None


def _describe_task(cluster_arn: str, task_arn: str) -> dict[str, Any]:
    resp = _ecs().describe_tasks(cluster=cluster_arn, tasks=[task_arn])
    failures = resp.get("failures") or []
    if failures:
        return {}
    tasks = resp.get("tasks") or []
    return tasks[0] if tasks else {}


def _exit_code_from_task(task: dict[str, Any]) -> int | None:
    containers = task.get("containers") or []
    for container in containers:
        code = container.get("exitCode")
        if code is not None:
            return int(code)
    return None


def classify(
    *,
    error_name: str | None,
    cause: str | None,
    cluster_arn: str | None,
    attempt: int,
) -> dict[str, Any]:
    """Pure classification logic, separated from the Lambda I/O shell so it
    is directly unit-testable without an event/context fixture."""
    if error_name == "States.Timeout":
        return _result("TIMEOUT", None, None, None, attempt)

    cause_parsed = _try_parse_cause(cause)
    stop_code = cause_parsed.get("stopCode") or cause_parsed.get("StopCode")
    stopped_reason = cause_parsed.get("stoppedReason") or cause_parsed.get("StoppedReason")
    exit_code = cause_parsed.get("exitCode")

    task_arn = _extract_task_arn(cause_parsed)
    if (stop_code is None or stopped_reason is None) and task_arn and cluster_arn:
        task = _describe_task(cluster_arn, task_arn)
        stop_code = stop_code or task.get("stopCode")
        stopped_reason = stopped_reason or task.get("stoppedReason")
        if exit_code is None:
            exit_code = _exit_code_from_task(task)

    if exit_code is not None:
        exit_code = int(exit_code)
        if exit_code == 137:
            return _result("RESOURCE", stop_code, stopped_reason, exit_code, attempt)
        mapped = ERROR_CLASS_BY_EXIT_CODE.get(exit_code)
        if mapped is not None:
            # A known, deterministic worker exit code: never retried.
            return _result(mapped, stop_code, stopped_reason, exit_code, attempt)

    reason_text = (stopped_reason or "") + (cause or "")
    if "OutOfMemoryError" in reason_text:
        return _result("RESOURCE", stop_code, stopped_reason, exit_code, attempt)
    if "CannotPullContainerError" in reason_text:
        # First occurrence: transient (a registry blip). A SECOND pull
        # failure at the same attempt count means the digest/endpoint
        # configuration itself is wrong -- Section 6.3's own distinction,
        # keyed on `attempt` since this classifier has no other memory.
        return _result(
            "TRANSIENT_INFRA" if attempt < 2 else "CONFIG",
            stop_code,
            stopped_reason,
            exit_code,
            attempt,
        )
    if stop_code == "TaskFailedToStart":
        return _result("TRANSIENT_INFRA", stop_code, stopped_reason, exit_code, attempt)
    if error_name == "AmazonECS.Unknown" or (error_name or "").startswith("ECS."):
        return _result("TRANSIENT_INFRA", stop_code, stopped_reason, exit_code, attempt)

    return _result("UNCLASSIFIED", stop_code, stopped_reason, exit_code, attempt)


def _result(
    error_class: str,
    stop_code: str | None,
    stopped_reason: str | None,
    exit_code: int | None,
    attempt: int,
) -> dict[str, Any]:
    return {
        "error_class": error_class,
        "stop_code": stop_code,
        "stopped_reason": stopped_reason,
        "exit_code": exit_code,
        "attempt": attempt,
    }


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    error_info = event.get("error_info") or {}
    result = classify(
        error_name=error_info.get("Error"),
        cause=error_info.get("Cause"),
        cluster_arn=event.get("cluster_arn"),
        attempt=int(event.get("attempt", 1)),
    )
    LOGGER.info(
        "classified task failure",
        extra={"job_id": event.get("job_id"), **result},
    )
    return result
