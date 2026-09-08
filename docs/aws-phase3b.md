# Phase 3b deployment summary

This PR implements the first production-shaped vertical slice:

`API Gateway -> control Lambda -> Step Functions Standard -> ECS Fargate -> S3/DynamoDB`

It runs the existing GJR-GARCH/Hansen skewed-t quantitative core through the Phase 2 worker. The control plane only validates, authorizes, records and orchestrates. Model calibration and human promotion remain separate Phase 4 work.

## Integrated review corrections

### Lambda package boundary

`scripts/package_control_plane.py` copies the control and adapter packages, only `domain/policies.py` plus its package initializer, and the shared worker exit-code table. It does not copy `domain/artifacts.py`, identity, serialization, services, the quant core, or the worker entry point. The package build preserves the hashed dependency lock, excludes the boto3 family supplied by the managed Lambda runtime, rejects ZIPs at or above 15 MB, extracts the completed ZIP, and imports all three handlers in `public.ecr.aws/lambda/python:3.13`. The check also rejects any loaded scientific module.

The package was previously measured at 2.6 MB and all handlers imported in that clean Linux runtime. This correction strengthens the boundary structurally by excluding scientific domain source files from the ZIP itself.

### Job state transitions

DynamoDB reads that drive control decisions use `ConsistentRead=True`. Updates are forward-only and require the job to exist:

- storing `execution_arn` can move `SUBMITTED` to `QUEUED`, but cannot regress `RUNNING` or a terminal status;
- cancellation can move only `SUBMITTED`, `QUEUED`, or `RUNNING` to `CANCELLED`;
- success and failure can move only `RUNNING` to their terminal state;
- attempt recording and retry require `RUNNING`;
- a worker invocation rechecks that the job is `RUNNING` before loading an artifact or computing.

DELETE records cancellation before calling `StopExecution`. Repeating DELETE on a cancelled job retries the stop call, so a transient stop failure is recoverable. Conditional failures preserve the terminal winner. Tests cover execution-before-ARN, cancellation during start, completion racing cancellation, replay after completion, and a cancelled job reaching the worker.

### Kill-switch

The cleanup module is mandatory in the DEV root. Terraform creates its Lambda, schedule, IAM policy, alarm and SNS topic before `module.network`; only then may the three billable interface endpoints be created. There is no disable flag.

Two independent server-side IAM denies use the one-time deadline:

- the submit role cannot call `states:StartExecution` at or after the deadline;
- the Step Functions role cannot call `ecs:RunTask` at or after the deadline.

The cleanup Lambda then performs two paginated task sweeps, calls `StopTask`, waits for `STOPPED` in batches of at most 100, discovers endpoints by their exact project Name tags, checks and deletes each endpoint, treats `deleting` as incomplete, inspects every `Unsuccessful` response, and polls each ID to confirmed deletion. Repeated invocation is safe. Partial failure or timeout publishes a dedicated alert and raises, allowing the independent Lambda-error alarm to fire. Its policy has no S3, DynamoDB, KMS or IAM data access.

Terraform apply is not transactional. The explicit `module.network depends_on module.demo_killswitch` closes the earlier ordering gap: an apply cannot create endpoints before the protection resources have been created successfully. After an apply, the operator still verifies the schedule ARN and SNS subscription as described in the runbook.

### Attempt-safe S3 results

Each worker attempt writes to a fresh immutable prefix:

`runs/{job_id}/attempts/{attempt_uuid}/...`

Only the stable `runs/{job_id}/manifest.json` is conditionally created with `If-None-Match: *`. Its output keys identify one complete attempt. A failed or losing attempt cannot overwrite any object referenced by the winning manifest, and an old manifest never points at data being replaced. All writes carry explicit SSE-KMS headers required by the bucket policy. Tests cover interruption, different concurrent payloads, interleaving, and preservation of an already-complete result.

### Artifact download classification

The S3 adapter preserves the original exception as the cause and separates:

| Condition | Worker class | Retry decision |
|---|---|---|
| missing object / 404 | `ARTIFACT_INTEGRITY` | no retry |
| access, credentials, signature or bucket configuration | `CONFIG` | no retry |
| throttling, timeout, endpoint or 5xx failure | `TRANSIENT_INFRA` | bounded retry |
| unknown download failure | `UNCLASSIFIED` | no retry |

The worker has explicit exit codes for these classes. The classifier reads both direct and nested ECS container exit codes. Exit 137 is `RESOURCE` only when ECS supplies OOM evidence; otherwise it is `UNCLASSIFIED`.

### Immutable image and provenance

`worker_compute` accepts only a validated `sha256:...` digest and sets the ECS image to `repository@digest`. The exact immutable reference and build Git SHA are recorded in the run manifest. The deployment workflow now waits for image push, passes the captured ECR digest to Terraform, builds and clean-room verifies the Lambda ZIP, and supplies an automatic four-hour cleanup deadline.

The Phase 3b infrastructure-management policy for `gha-ci-dev` is scoped to project-prefixed Lambda, Step Functions, HTTP API, Scheduler, SNS and CloudWatch resources. Its existing permissions-boundary and self-escalation guardrails remain in force.

## Local validation and remaining live checks

The previous Linux acceptance on this branch passed all 10 mandatory container checks with zero skips: linux/amd64, non-root UID, single-thread runtime, 682 MB image and exact golden replay digest `sha256:637920e584b8e82449a67b84bfc39b73528256aa6518d8ca8280087b9fb3c255`. The four native-Windows digest failures were reproduced on `main` in the same environment and did not occur in the Linux image.

The latest follow-up was validated with the affected Python and infrastructure tests, Ruff, compilation, HCL parsing and mypy in the available environment. Docker and Terraform execution are unavailable in this review environment, so the earlier real Linux container evidence is retained and not represented as rerun. No AWS API was called.

A real bounded deployment must still prove:

1. `terraform plan/apply` with live AWS IAM and provider behavior;
2. the ZIP import and size in the deployment runner;
3. a real Fargate golden replay and recorded image/task provenance;
4. `scripts/smoke.py` through the IAM-authorized API;
5. cleanup stopping real tasks and confirming real endpoint deletion;
6. measured cost.

The PR remains open. No AWS resources were created by these corrections.
