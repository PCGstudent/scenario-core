# AWS demo runbook

Use this runbook to demonstrate the complete Phase 3b path once or twice, then remove it. The target is approximately EUR 10; the configured AWS Budget is an alert, while the mandatory one-time kill-switch limits unattended compute and endpoint time.

## Current account state

Already present in DEV account `758895552145`, region `eu-west-1`:

- Budget `4xtra-dev-demo`, USD 10/month, actual alerts at 50%, 80% and 100% plus forecast at 100%, addressed to `todosgranja@gmail.com`;
- state bucket `4xtra-dev-tfstate-758895552145`;
- bootstrap KMS alias `alias/4xtra-dev-tfstate`.

The Phase 3a foundation and Phase 3b vertical slice have not yet been applied. Do not infer real AWS acceptance from local tests.

## Cost model

The dominant cost is three interface endpoints in two availability zones: approximately USD 0.06/hour combined, or about USD 43.80 for a full 730-hour month. A four-hour window is about USD 0.24 for endpoints. Environment and bootstrap KMS keys contribute roughly USD 2/month while retained; low-volume S3, DynamoDB, ECR, API Gateway, Lambda, Step Functions and logs add small usage charges.

At USD 0.06/hour, USD 10 corresponds to roughly 166 endpoint-hours before flat items, around six to seven continuous days. The four-hour deadline leaves a wide margin. Pricing and billing telemetry must still be checked after the real run.

AWS Budgets does not impose a hard cap and billing data can lag. The server-side deadline and cleanup schedule are the spending controls.

## Safety properties

The DEV root always creates `module.demo_killswitch`; there is no disable flag. `module.network` depends on it, so Terraform must successfully create the cleanup Lambda, schedule, IAM policy, alarm and SNS topic before it may create billable interface endpoints.

The same UTC deadline is enforced by explicit IAM denies on `states:StartExecution` and `ecs:RunTask`. At the deadline, cleanup performs two complete paginated task sweeps, waits for every stopped task, then discovers the three interface endpoints by exact Name tags and polls every endpoint to confirmed deletion. Partial failures raise and notify SNS. The cleanup role cannot modify S3, DynamoDB or KMS.

Terraform apply is not atomic. Dependency ordering prevents the dangerous direction of partial failure: endpoints cannot precede protection. Immediately after apply, still verify the schedule ARN and confirm the SNS email subscription. If apply fails, inspect state and AWS before retrying; never assume rollback.

## First deployment

Use the authenticated `4xtra-dev` profile and verify:

```bash
aws sts get-caller-identity --profile 4xtra-dev
aws configure get region --profile 4xtra-dev
```

Expected account and region are `758895552145` and `eu-west-1`.

The first deployment has an ECR bootstrap dependency: the repository must exist before the immutable worker image can be pushed. With direct human credentials, prepare the Lambda ZIP and use a targeted plan for the foundation/ECR repository. Because root variables are required even for a target, pass syntactically valid placeholder values and review that no network endpoint is in the plan. Never target `module.network`.

After the repository exists:

1. Run `scripts/run_container_acceptance.sh` on Linux. It must pass with zero skips and remain below 700 MB.
2. Build the image with the real Git SHA, push one tag, then resolve the registry digest with `aws ecr describe-images`.
3. Run `python scripts/package_control_plane.py --output build/control-plane.zip`. Its clean-room Lambda import and size checks are mandatory.
4. Choose a one-time UTC cleanup expression approximately four hours ahead: `at(YYYY-MM-DDThh:mm:ss)`.
5. Create a saved, untargeted Terraform plan with the real worker digest, absolute ZIP path, alert email and cleanup expression.
6. Review the plan: no NAT gateway or internet gateway; three interface endpoints; immutable `repository@sha256` task image; mandatory cleanup resources created before the network; zero unexpected destroys.
7. Apply the saved plan.
8. Immediately verify `terraform output -raw demo_killswitch_schedule_arn` and inspect the schedule state/time. Confirm both SNS subscription emails.

`.github/workflows/deploy-dev.yml` implements the repeat-deployment form: it builds and pushes first, captures the digest, verifies the ZIP, computes a four-hour deadline, and only then plans/applies. It remains `workflow_dispatch` only. The repository variable `ALERT_EMAIL` must be set. The known repository-level Actions startup failure is outside this runbook and is not investigated here.

## Demo

Upload the frozen Phase 1 artifact with explicit SSE-KMS using `scripts/seed_registry.py --kms-key-arn ...`, then run the connectivity probe and `scripts/smoke.py` against the Terraform API output.

A successful demonstration must retain:

- commit SHA and ECR digest;
- API job ID and Step Functions execution ARN;
- manifest with exact `worker_image_ref`, `worker_git_sha`, request and result digests;
- `risk_report.json` and the returns file digest;
- task ARN, timing and final DynamoDB status;
- Terraform outputs and cleanup deadline.

Store account-specific evidence outside Git. Commit only the small non-sensitive summary intended for review.

## Shutdown

Do the normal Terraform teardown well before the deadline. Preserve evidence first. Run a saved destroy plan for the Phase 3b/runtime and network resources, review it, then apply it. The bootstrap state bucket and protected artifact resources have lifecycle guardrails; removing those later requires an explicit reviewed change and object/version cleanup.

After teardown, verify directly:

- no running or pending tasks in either project cluster;
- no `4xtra-dev-ecr-api`, `4xtra-dev-ecr-dkr` or `4xtra-dev-logs` interface endpoint;
- no active Step Functions execution;
- the expected retained buckets/keys only;
- the Budget remains active.

If the scheduled cleanup fired first, its out-of-band deletion will make Terraform propose endpoint recreation on an ordinary apply. Do not run that apply. Refresh/plan, then destroy the remaining managed resources. Repeated cleanup is safe.

## Recreating later

Keep the repo commit, artifact identity, worker digest or rebuild inputs, request/seed, evidence and Terraform state. A later demo uses a new immutable image digest and a new one-time deadline. Never reuse an expired deadline.
