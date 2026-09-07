"""Phase 3a acceptance criterion 3 (IMPLEMENTATION_PLAN.md):
"The connectivity probe proves S3/DynamoDB/ECR/Logs are reachable and the
internet is not."

Runs INSIDE the VPC's private subnet, attached to the `task` security
group created by modules/network -- this is a one-off ECS Fargate task
(see the "connectivity-probe" resources in infra/terraform/envs/dev/
probe.tf), not a long-lived service. It exists specifically to surface an
endpoint or security-group misconfiguration here, in Phase 3a where
nothing yet depends on it, rather than silently in Phase 3b (Section 24's
Phase 3a "Risks" entry).

Every check is independent and reported individually; the script exits 0
only if every AWS-service check passed AND the public-internet check
failed to connect (i.e. reachability was correctly denied). A non-zero
exit means either an AWS service that should be reachable was not, or the
public internet was unexpectedly reachable -- both are configuration bugs
worth fixing before anything depends on this network.

This is source code prepared for a real run; running it is exactly the
"connectivity probe proves reachability" acceptance criterion, and this
docstring makes no claim that it has been run.
"""

# ruff: noqa: TID251 -- AGENTS.md invariant 28 bans boto3/botocore from the
# quantitative core and the pure domain layer; this script is neither --
# every line in it exists to call AWS services, matching the "adapters/ the
# ONLY place boto3 appears in the data plane" carve-out the architecture
# plan already describes for that exact purpose (Section 22).

from __future__ import annotations

import json
import os
import socket
import sys
import time
from dataclasses import dataclass, field


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str
    duration_ms: float = field(default=0.0)


def _timed(name: str, fn) -> CheckResult:
    t0 = time.monotonic()
    try:
        detail = fn()
        return CheckResult(name, True, detail, (time.monotonic() - t0) * 1000.0)
    except Exception as exc:  # noqa: BLE001 -- a probe reports every failure, it never crashes
        return CheckResult(
            name, False, f"{type(exc).__name__}: {exc}", (time.monotonic() - t0) * 1000.0
        )


def check_s3(bucket_names: list[str]) -> str:
    import boto3

    client = boto3.client("s3")
    for bucket in bucket_names:
        client.head_bucket(Bucket=bucket)
    return f"HeadBucket succeeded for {bucket_names}"


def check_dynamodb(table_names: list[str]) -> str:
    import boto3

    client = boto3.client("dynamodb")
    statuses = []
    for table in table_names:
        response = client.describe_table(TableName=table)
        statuses.append(response["Table"]["TableStatus"])
    return f"DescribeTable succeeded for {table_names} (status: {statuses})"


def check_ecr(repository_name: str) -> str:
    import boto3

    client = boto3.client("ecr")
    client.get_authorization_token()
    client.describe_repositories(repositoryNames=[repository_name])
    return f"GetAuthorizationToken + DescribeRepositories succeeded for {repository_name!r}"


def check_logs(log_group_name: str) -> str:
    import boto3
    from botocore.exceptions import ClientError

    client = boto3.client("logs")
    try:
        client.create_log_group(logGroupName=log_group_name)
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "ResourceAlreadyExistsException":
            raise
    client.put_log_events(
        logGroupName=log_group_name,
        logStreamName=_ensure_log_stream(client, log_group_name),
        logEvents=[
            {
                "timestamp": int(time.time() * 1000),
                "message": "connectivity_probe reachability check",
            }
        ],
    )
    return f"CreateLogGroup/PutLogEvents succeeded for {log_group_name!r}"


def _ensure_log_stream(client, log_group_name: str) -> str:
    stream_name = "connectivity-probe"
    try:
        client.create_log_stream(logGroupName=log_group_name, logStreamName=stream_name)
    except client.exceptions.ResourceAlreadyExistsException:
        pass
    return stream_name


def check_public_internet_is_unreachable(
    host: str, port: int, timeout_seconds: float
) -> str:
    """The inverse of every check above: this one is expected to FAIL to
    connect. A successful connection here is the failure -- it means the
    "no IGW, no NAT" network design does not hold in practice."""
    try:
        with socket.create_connection((host, port), timeout=timeout_seconds):
            pass
    except OSError as exc:
        return f"connection to {host}:{port} correctly failed ({type(exc).__name__}: {exc})"
    raise AssertionError(
        f"connection to {host}:{port} SUCCEEDED -- the public internet is reachable, "
        "which must never be true for this network (Section 14.1 point 5)"
    )


def main() -> int:
    artifacts_bucket = os.environ["PROBE_ARTIFACTS_BUCKET"]
    runs_bucket = os.environ["PROBE_RUNS_BUCKET"]
    scenario_jobs_table = os.environ["PROBE_SCENARIO_JOBS_TABLE"]
    model_registry_table = os.environ["PROBE_MODEL_REGISTRY_TABLE"]
    ecr_repository = os.environ["PROBE_ECR_REPOSITORY"]
    log_group = os.environ.get("PROBE_LOG_GROUP", "/4xtra/connectivity-probe")
    public_host = os.environ.get("PROBE_PUBLIC_HOST", "1.1.1.1")
    public_port = int(os.environ.get("PROBE_PUBLIC_PORT", "443"))
    public_timeout = float(os.environ.get("PROBE_PUBLIC_TIMEOUT_SECONDS", "5"))

    results = [
        _timed("s3_reachable", lambda: check_s3([artifacts_bucket, runs_bucket])),
        _timed(
            "dynamodb_reachable",
            lambda: check_dynamodb([scenario_jobs_table, model_registry_table]),
        ),
        _timed("ecr_reachable", lambda: check_ecr(ecr_repository)),
        _timed("logs_reachable", lambda: check_logs(log_group)),
        _timed(
            "public_internet_unreachable",
            lambda: check_public_internet_is_unreachable(
                public_host, public_port, public_timeout
            ),
        ),
    ]

    report = {
        "checks": [
            {
                "name": r.name,
                "ok": r.ok,
                "detail": r.detail,
                "duration_ms": round(r.duration_ms, 1),
            }
            for r in results
        ],
        "all_passed": all(r.ok for r in results),
    }
    print(json.dumps(report, indent=2))
    return 0 if report["all_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
