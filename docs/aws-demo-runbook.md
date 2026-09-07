# AWS demo runbook (one-off, budget-bounded, fully torn down afterward)

Purpose: run the **complete** platform (API Gateway → Lambda → Step
Functions → Fargate → S3/DynamoDB, Phase 3b's own vertical slice) on AWS
once or twice for study and a possible demonstration, inside a target of
**~EUR 10 for the demo window**, then **remove every AWS resource this
demo creates** — no permanently-on DEV environment, and no residual cost
accepted as a permanent fixture by default. Where AWS itself imposes a
resource that cannot be deleted instantly (a KMS key's mandatory deletion
window, an S3 bucket's own versioned-object cleanup), that is disclosed
explicitly below as a **waiting period**, not silently absorbed into "the
cost of doing business."

## 0. What already exists vs. what is only planned

**Already applied to the real AWS account (758895552145, eu-west-1),
executed in a prior turn — real, billable, present right now:**

- AWS Budget `4xtra-dev-demo`: $10.00/month, 4 notifications (50/80/100%
  actual + 100% forecast) to `todosgranja@gmail.com`. **Correction from
  the prior turn**: the account accepting `Unit: "USD"` for this budget
  confirms only that USD is a valid *denomination for the budget object*
  — AWS Budgets does not require a budget's currency to match the
  account's actual invoice currency, and no Cost Explorer/Billing check
  was ever run to confirm the real billing currency independently. Treat
  "the account bills in USD" as unconfirmed, not established.
- Terraform bootstrap: state bucket `4xtra-dev-tfstate-758895552145` +
  CMK `alias/4xtra-dev-tfstate`. Local state at
  `infra/terraform/bootstrap/terraform.tfstate`, backed up to
  `C:\Users\user\4xtra-terraform-local-state\bootstrap\`.

**Written in this turn, present in the repo, NOT applied to AWS —
Terraform code and application code only, zero AWS resources created by
any of it yet:**

- `infra/terraform/envs/dev` now wires `modules/{worker_compute,
  job_orchestrator, job_api, observability}` alongside the existing Phase
  3a modules — the full Phase 3b vertical slice (`terraform validate`
  passes; nothing has been planned or applied against the real backend).
- `infra/terraform/modules/demo_killswitch` — the independent auto-cleanup
  mechanism (section 3), wired into `envs/dev` CONDITIONALLY
  (`var.demo_killswitch_enabled`, default `false`) so a plain `apply`
  never creates it, but a demo apply creates it atomically alongside
  `module.network`'s endpoints in the same plan (section 3's own design
  note on why a separate, later apply was rejected).
- `src/scenario_platform/control/*` (three Lambda handlers), the new
  `simulate --job-id` mode on the worker CLI, `adapters/{s3_store,
  job_store}.py`.
- `scripts/{seed_registry,smoke,package_control_plane}.py`.

**Not created, not started, no cost of any kind:** the VPC, its 3
interface endpoints, the ECS cluster/task, the API Gateway, the Lambdas,
the Step Functions state machine, the demo-cleanup schedule. This is the
entire point of "prepare everything locally first" — every AWS-side
finding in this document is arithmetic against the design, not a
measurement of something already running.

## 1. What "the demo" now covers (no scope reduction)

The prior turn's runbook proposed substituting a single worker-only S3
round-trip for the full Phase 3b slice, on the grounds that the slice did
not exist as code. That code now exists (this turn's implementation): a
real `POST /scenario-jobs` → Step Functions → Fargate → S3/DynamoDB path,
with idempotency, the approval predicate, admission limits, and the
`ClassifyFailure`/`RetryDecision` failure-classification loop, all unit
tested against `moto` (`tests/scenario_platform/`, `tests/infra/`). The
demo therefore exercises the actual architecture, not a stand-in for it.

**What still cannot be exercised locally** (AWS-only, listed once here
and not repeated per section):

- Whether `terraform apply` for `envs/dev` + `demo_killswitch` actually
  succeeds against real AWS (IAM `PassRole`/`RunTask` conditions are the
  classic first-deployment failure mode per `IMPLEMENTATION_PLAN.md`
  Section 24's own risk note).
- Whether a submitted job actually completes on real Fargate and produces
  a byte-identical array to the local golden fixture (Phase 3b acceptance
  criterion 2) — `tests/scenario_platform/test_worker_job_id_mode.py`
  proves the code path is correct against a mocked S3/DynamoDB, not that
  real Fargate reproduces the same floating-point result (a real, but
  separate, risk `docker/worker.Dockerfile`'s own Phase 2 acceptance
  criteria already flag independently of Phase 3b).
- Whether the demo-cleanup Lambda's `ec2:DeleteVpcEndpoints` /
  `ecs:StopTask` calls actually work against real endpoint/task state
  machines (moto's EC2/ECS mocks do not model endpoint deletion timing or
  task lifecycle transitions with full fidelity).
- The true account billing currency (see section 0's correction) and
  therefore whether $10.00 is really <= EUR 10 or a different amount.
- Real measured cost, only obtainable from Cost Explorer/the Budget's own
  `CalculatedSpend` after the account has actually incurred it — Cost
  Explorer remains **disabled** on this account as of the prior turn
  (console-only to enable, ~24h propagation).

`scripts/smoke.py` is the tool for the first two once a real deployment
exists; nothing else in this list has a script, because nothing else can
be checked without also having deployed.

## 2. Cost model

### 2.1 Standing cost while the demo infrastructure exists (24/7 rate)

| Item | Basis | Monthly rate |
|---|---|---|
| 3 VPC interface endpoints (ecr.api, ecr.dkr, logs) × 2 AZ | $0.01/hr each = $0.06/hr combined | ≈ $43.80 |
| Environment KMS CMK (`modules/kms`) | 1 key | $1.00 |
| Bootstrap KMS CMK | already applied, independent of the demo | $1.00 |
| `artifact_store` S3 (artifacts + runs, near-empty) | | < $0.01 |
| `job_store` DynamoDB (2 tables, on-demand + PITR) | | < $0.05 |
| ECR repository | $0 until an image is pushed, then | ≈ $0.50 |
| Step Functions Standard, API Gateway HTTP API, Lambda | pay-per-use, negligible at demo volume | < $0.05 |
| CloudWatch log groups (worker, classify_failure, api-submit, api-status, sfn executions) | 30-day retention, near-empty | < $0.10 |
| `demo_killswitch`'s Lambda + EventBridge Scheduler + SNS topic | one-time schedule, sub-second invocation | ≈ $0.00 |
| **Total, 24/7** | | **≈ $46.5/month** |

This is why the demo is a **window**, not a standing deployment: at this
rate, EUR 10 (~$11-12) is consumed in roughly **6 hours** of continuous
uptime. Everything below exists to bound that window and then reverse it
completely.

### 2.2 A bounded demo window

At $0.06/hr for the dominant line (the interface endpoints; everything
else in the table above is flat per-month regardless of hours up within
one month):

| Window | Endpoint cost | Plus the ≈ $2/month flat items (prorated) | Total |
|---|---|---|---|
| 2 hours (one test run + one real demo) | $0.12 | negligible | ≈ $0.15 |
| 8 hours (a full working day) | $0.48 | negligible | ≈ $0.55 |
| 24 hours (a full day forgotten) | $1.44 | negligible | ≈ $1.5 |

A single 2-8 hour demo window costs cents, not euros. **The only way to
approach the EUR 10 figure is to leave the infrastructure standing for
many hours unattended** — exactly the failure mode section 4's
auto-cleanup exists to bound, independent of anyone remembering.

### 2.3 What a monthly AWS Budget does and does not tell you here

- **Not a hard cap.** Budgets alert; they never block spend. Section 4's
  auto-cleanup is the only mechanism here that actually stops the meter.
- **Billing-data delay.** Actual usage typically lags what
  Budgets/Cost Explorer report by hours, sometimes longer near a period
  boundary — an alert can arrive after the fact, not preventatively.
- **Calendar-month scoped, not session-scoped.** `4xtra-dev-demo` resets
  its 0% baseline on the 1st of each calendar month. A demo window that
  straddles a month boundary (stood up on the 30th, torn down on the
  2nd) is split across two separate monthly budget periods — neither
  period's percentage reflects the demo's own total in that case. For a
  single continuous demo, prefer the CLI/console spend view for that
  specific date range over trusting the budget percentage alone.
- **Currency uncertainty stands** (section 0): a $10.00 budget is not
  confirmed to be <= EUR 10 until the account's real billing currency is
  verified (Cost Explorer, once enabled, or a Billing Preferences check).

## 3. Auto-cleanup mechanism (built: `infra/terraform/modules/demo_killswitch`)

Constraint carried over from the prior turn and still binding: **must not
depend on GitHub Actions** (its runs are currently failing at startup,
not investigated per standing instruction) **or on any local machine**
being on.

**What is built** (`modules/demo_killswitch/{main.tf,lambda/cleanup.py}`,
not yet applied), in the order the Lambda actually runs it:

1. **Deregister the watched task definition(s) first** (`ecs:
   DeregisterTaskDefinition`, scoped to exactly the ARN(s) it is told to
   watch). This is the step that actually stops NEW compute from being
   placed while cleanup runs: Step Functions' `RunSimulation` state
   references one fixed task-definition ARN baked in at `terraform
   apply` time, and `ecs:RunTask` against a deregistered revision fails
   immediately at the ECS API level — a submission arriving mid-cleanup
   cannot place a task, no matter how many times its own bounded retry
   loop tries (that failure is classified `TRANSIENT_INFRA` by
   `classify_failure.py`'s existing "unrecognised ECS error" branch, so
   such a submission still burns its retry budget before failing closed
   — compute is blocked from being *placed*, not from being *retried at
   the state-machine level*, a real but bounded, disclosed limit).
   Confirmed by re-`DescribeTaskDefinition`-ing afterward, not assumed
   from the deregister call's own HTTP success.
2. **Stop already-running Fargate tasks**, cluster task lists paginated
   in full (not just the first page), each `StopTask` confirmed via
   boto3's `tasks_stopped` waiter — not fire-and-forget. Deleting the
   endpoints before this would cut a running task off from DynamoDB/S3/
   ECR/Logs without stopping its billed compute time; "the endpoints are
   gone" is not the same claim as "the compute is gone."
3. **Delete the interface endpoints**, each id's state checked
   **individually** (a single batched `DescribeVpcEndpoints` call fails
   entirely if even one of several ids is unrecognised, which would
   otherwise mask the real state of every other, still-billing,
   endpoint), polled until each genuinely reports `deleted` — `deleting`
   is still in progress, not the same as done, and is not mistaken for
   it. The `Unsuccessful` list `DeleteVpcEndpoints` itself returns is
   inspected, not ignored.

The role behind all of this can, and only can: the three EC2/ECS actions
above (each individually allowlisted in
`infra/terraform/policy/resource-star-allowlist.yaml` with its own AWS
Service Authorization Reference citation) plus `sns:Publish` to its own
one failure topic. No `s3:*`, `dynamodb:*`, `kms:*`, `iam:*` — verified by
reading the policy document, not by intent: no statement in
`modules/demo_killswitch/main.tf` names any of those services at all, so
there is nothing to accidentally exercise.

- **Idempotent** at every step: each one describes current state before
  acting and treats "already gone/already inactive/already stopped" as
  success, not as an error to retry into.
- **On failure**: publishes to its own SNS topic (`todosgranja@gmail.com`),
  naming the specific errors, then re-raises so CloudWatch's own `AWS/
  Lambda Errors` metric fires too — a second, independent alarm
  (`aws_cloudwatch_metric_alarm.cleanup_lambda_errors`) covers the case
  where the Lambda fails before it can even publish that message itself.
- **Never deletes results, state, or job records** — by construction
  (no IAM permission to), not by the code choosing not to call something
  it could have called.
- **This is a safety net, not the primary teardown path.** The intended
  flow is manual teardown within the window (section 6); the schedule
  only matters if that is missed.

### 3.1 The one window this cannot close: a partial `terraform apply`

If the same `terraform apply` that creates `module.network` (the costly
endpoints) also creates `modules/demo_killswitch` (the schedule that
watches them) — as section 4 below instructs — an apply that fails
**after** the endpoints exist but **before** the schedule/Lambda do
leaves a real, unprotected window: costly resources with no auto-cleanup
watching them yet.

**Mitigation, not a guarantee**:

- Never apply `module.network` with `-target` on its own for a demo —
  always a single, untargeted `terraform apply` covering both
  `module.network` and `modules/demo_killswitch` together, so Terraform's
  own dependency graph either creates both or (on failure) leaves the
  error visible before declaring the apply done.
- **Immediately after any apply that touches the demo resources**, before
  doing anything else, run `terraform state list | grep demo_killswitch`
  (or the module's own `terraform output schedule_arn`) and confirm it
  resolves. If the apply reported an error and this comes back empty,
  the endpoints may already exist unprotected — proceed straight to
  section 6's manual teardown for `module.network` rather than walking
  away or retrying blind.
- This is a process discipline, not a code fix: no Terraform provider
  feature makes a multi-resource `apply` atomic across a partial-failure
  boundary. Treat "confirm the schedule exists" as a mandatory step of
  section 4, not an optional sanity check.

**Reconciling Terraform after this Lambda fires** (it acts outside any
`terraform apply`/`destroy`, so state and reality diverge the moment it
runs):

1. The next `terraform plan` against `envs/dev` will show the 3 interface
   endpoints as needing to be created again — Terraform's own refresh
   detects that a resource it manages is gone from AWS and proposes
   recreating it, exactly the same as any other out-of-band deletion.
2. **Do not** `terraform state rm` them — that is unnecessary surgery
   for a `plan`/`apply`/`destroy` cycle that already handles "resource
   is gone" correctly on its own.
3. If the intent is to actually tear the rest of the demo down (the
   normal case, since the Lambda firing means the manual teardown was
   missed): run `terraform destroy -target=module.network` anyway. It
   will report 0 real deletions for the 3 endpoints (already gone) and
   correctly remove everything else in that module (VPC, subnets,
   security groups, flow-log bucket) that the Lambda did not touch.
4. If the intent is instead to resume the demo: `terraform apply` (with
   `demo_killswitch_enabled=true` and a fresh `demo_schedule_expression`)
   recreates the 3 endpoints with fresh IDs and re-wires
   `module.demo_killswitch`'s `vpc_endpoint_ids` to them automatically
   (it is wired from `module.network`'s own outputs, section 3's design
   note -- nothing to update by hand).

## 4. Demo window plan

**A real chicken-and-egg step, disclosed rather than glossed over**:
`module.worker_compute`'s task definition takes the worker image's digest
as a required Terraform INPUT variable (section 6's own fix, replacing a
mutable `:tag` reference) — but the ECR repository that image gets pushed
to (`module.worker_image`) does not exist until `envs/dev` is applied at
least once. A single, one-shot `terraform apply` therefore cannot work
the very first time; the connectivity probe's own task definition
(`probe.tf`) sidesteps this by referencing a mutable `:probe` tag instead,
resolved by ECS at pull time rather than baked into a Terraform value —
`worker_compute` deliberately does not take that shortcut (section 6's
whole point). Two applies, the first narrow, are required on a truly
first-ever deployment only:

1. `aws sts get-caller-identity --profile 4xtra-dev` — confirm account
   758895552145, region eu-west-1; re-authenticate if the session expired.
2. `cd infra/terraform/envs/dev && terraform init && terraform apply \
   -target=module.worker_image` — creates ONLY the ECR repository (and
   its own KMS/CI-identity dependencies), so there is somewhere to push
   to. Skip this step on any subsequent demo run where the repository
   already exists.
3. Build and push the worker image (now including `boto3` per
   `requirements/worker-image.in`) to that repository, manually, once —
   same handoff pattern as the existing connectivity probe.
4. `aws ecr describe-images --repository-name <name> --image-ids
   imageTag=<pushed tag> --query 'imageDetails[0].imageDigest' --output text`
   — capture the real digest (`sha256:...`).
5. Compute a deadline, e.g. `date -u -d "+4 hours" +%Y-%m-%dT%H:%M:%S`
   (UTC, no offset suffix -- `aws_scheduler_schedule` wants `at(...)`
   format).
6. `terraform plan \
   -var worker_image_digest=<digest from step 4> \
   -var control_plane_package_path=<scripts/package_control_plane.py's output> \
   -var demo_killswitch_enabled=true \
   -var demo_schedule_expression="at(<deadline from step 5>)" \
   -out=dev.tfplan` — **review the plan** (expected: the remaining Phase
   3a + full Phase 3b resource set PLUS `module.demo_killswitch`'s
   schedule/Lambda/SNS topic, all in the SAME plan; 0 destroys, and
   `module.worker_image` shows no changes since step 2 already applied
   it). This is the point at which "does it actually apply" (section 1's
   first AWS-only unknown) gets answered for everything else, and --
   because `demo_killswitch_enabled=true` is part of this same
   plan/apply -- the endpoints and their auto-cleanup are created
   atomically together (section 3's own design note).
7. `terraform apply dev.tfplan`.
8. **Immediately**, before anything else: `terraform output -raw
   demo_killswitch_schedule_arn` and confirm it prints a real ARN, not
   empty. If the apply reported any error and this comes back empty,
   follow section 3.1's mitigation now, before proceeding.
9. `python scripts/seed_registry.py ...` — register the one frozen
   artifact this demo runs against (Section 25's "pre-registered frozen
   artifact"; Phase 4's calibration/promotion workflow does not exist).
10. Run the existing connectivity probe first (zero new cost) to confirm
    the network is genuinely reachable.
11. `python scripts/smoke.py --api-endpoint $(terraform output -raw
    api_endpoint) --golden-digest sha256:637920e5...fb3c255` — submit,
    poll, fetch results, verify the digest (section 1's second AWS-only
    unknown).
12. Capture evidence (section 5).
13. Run the final shutdown sequence (section 6) well inside the deadline
    from step 5, so the scheduled Lambda finds nothing left to do.

## 5. Evidence to capture before any teardown

Copied to `C:\Users\user\4xtra-terraform-local-state\demo-evidence\<date>\`
(durable, outside git, since some of this carries account-specific
ARNs/IDs) **and** the small, non-sensitive summary files committed to
`reports/` so the demo is citable without AWS access:

- `scripts/smoke.py`'s own console output (job_id, digest, manifest
  fields, elapsed time).
- The real job's `manifest.json` + `risk_report.json` (small, safe to
  commit under `reports/`); `returns.npy` stays in the durable local
  folder only (larger, and reproducible from the manifest + artifact).
- `terraform output` (full) from `bootstrap`, `envs/dev` and
  `demo_killswitch` — resource IDs/ARNs, not secrets, consistent with
  `infra/terraform/bootstrap/README.md`'s own framing of what state
  contains.
- `git rev-parse HEAD` and the pushed image's ECR digest
  (`aws ecr describe-images`) — what "recreate the demonstration later"
  actually keys off, since the ECR lifecycle policy will eventually
  expire old images.
- Whatever the AWS Budget / Cost Explorer (once enabled) reports for the
  demo's date range — the only way to replace "estimated" with
  "measured" in this document, and the only way to finally resolve the
  currency uncertainty in section 0.

## 6. Final shutdown sequence

Two distinct destinations, not to be conflated:

**(A) End of one demo session, keeping the ability to run another one
later without re-registering the artifact.** This is what section 4's
`demo_killswitch` also targets, and what the sequence below performs by
default.

**(B) Truly zero residual AWS footprint** (the whole project is done,
not just this session) — requires editing two `prevent_destroy =
true` lifecycle blocks that exist specifically to prevent exactly this
from happening by accident. That edit is **not performed by this
runbook, this script, or any automated step** — it is a deliberate,
reviewed code change (its own commit/PR), per this turn's explicit
instruction. Sequence (B) is described at the end of this section for
when that review has actually happened, not as something to run now.

### 6A. Tear down the demo, keep the reusable base

```bash
cd infra/terraform/envs/dev
terraform destroy -target=module.demo_killswitch   # the schedule/Lambda/SNS topic -- no longer watching anything after this
terraform destroy -target=module.network           # the 3 interface endpoints -- the dominant cost line
terraform destroy -target=module.worker_compute -target=module.job_orchestrator -target=module.job_api -target=module.observability
# Left standing deliberately (all $0 or near-$0 at rest):
#   module.kms, module.artifact_store, module.job_store, module.worker_image,
#   module.ci_oidc, infra/terraform/envs/dev/probe.tf's resources.
# Destroying/recreating ci_oidc means re-wiring the GitHub Actions role from
# scratch next time, for no cost saving. artifact_store's two buckets carry
# prevent_destroy and would fail here anyway (by design -- see 6B).
```

Residual after 6A, standing indefinitely at the rate in section 2.1's
flat-cost rows (~$2.5-3/month): the bootstrap state bucket + CMK
(already existed before this turn), the environment CMK, the near-empty
S3/DynamoDB/ECR resources, the CI OIDC role, the probe cluster/task
definition (registered, $0 idle). **This is not "acceptable permanent
cost by default"** — it is what remains after choosing (A) specifically
to keep the demo re-runnable; choosing (B) below removes it too.

### 6B. Full removal (only after (A), evidence captured, and this edit reviewed)

1. **Reviewed code change, its own commit**: remove `lifecycle {
   prevent_destroy = true }` from `infra/terraform/bootstrap/main.tf`'s
   state-bucket resource and from both buckets in
   `infra/terraform/modules/artifact_store/main.tf`. Get this reviewed
   before applying anything — it is the one guardrail in this codebase
   whose entire purpose is to make an accidental full teardown fail
   loudly instead of succeeding quietly.
2. `terraform destroy` (unscoped) on `envs/dev` — now succeeds, removing
   everything Terraform manages there, including the artifact/runs
   buckets. **Waiting period**: both buckets have versioning enabled; a
   bucket with any object versions in it (including delete markers) does
   not delete via a plain `DestroyBucket` call — Terraform's S3
   provider handles this for an empty bucket automatically, but if any
   real run's outputs were ever written and not explicitly purged first,
   add `force_destroy = true` **as its own reviewed change**, or manually
   empty all versions (`aws s3api list-object-versions` +
   `delete-objects`) before this step.
3. `cd ../../bootstrap && terraform destroy` — removes the state bucket
   and the bootstrap CMK **last**, since `envs/dev`'s own remote state
   lives in that bucket until step 2 has fully run.
4. **Waiting period, disclosed, not absorbed**: both CMKs (bootstrap and
   environment) enter AWS KMS's mandatory pending-deletion window
   (`deletion_window_in_days`, 30 by default in this codebase) once
   `terraform destroy` schedules them — they are **not** immediately
   gone, and AWS continues to bill a scheduled-for-deletion CMK at its
   normal per-key rate until the window elapses. True zero KMS cost is
   reached 30 days after step 3, not at step 3 itself.
5. After step 4's window elapses, the account's continuing cost from
   this project is $0.00.

Do not perform 6B as a way to "clean up" between ordinary demo sessions
— it destroys the artifact registry and the reusable Terraform state,
requiring `scripts/seed_registry.py` and a fresh bootstrap to run this
demo again from nothing. It is the end of the project, not the end of a
session.

## 7. Recreating this demo later (after 6A only)

1. `aws sts get-caller-identity --profile 4xtra-dev` — confirm session/
   account/region.
2. The ECR repository already exists (never destroyed under 6A), so
   section 4's step 2 (targeted `module.worker_image` apply) is not
   needed again -- but a digest is still required every time: re-push
   the worker image if the ECR lifecycle policy already expired the old
   one (`image_count_to_retain`, 5 in DEV), then re-run section 4 step 4
   (`aws ecr describe-images ...`) to capture the current digest even if
   the image itself did not change (a lifecycle-expired-and-repushed
   image gets a new digest).
3. `cd infra/terraform/envs/dev && terraform init && terraform plan \
   -var worker_image_digest=<digest from step 2> \
   -var control_plane_package_path=... \
   -var demo_killswitch_enabled=true \
   -var demo_schedule_expression="at(<fresh deadline>)" \
   -out=dev.tfplan` — the bootstrap bucket and the reusable base from 6A
   already exist, so this only recreates what was destroyed (endpoint
   count, `module.demo_killswitch` wired from this apply's own module
   outputs, nothing to copy by hand).
4. Review, then `terraform apply dev.tfplan`, then section 4 step 8's
   `demo_killswitch_schedule_arn` confirmation.
5. `scripts/seed_registry.py` does not need to re-run if the artifact is
   still present at its S3 prefix (bucket was never destroyed under 6A).
6. Follow section 4 from step 10 onward, then section 6A again afterward.

This runbook is the durable instruction set for recreating the
demonstration independent of any particular chat session, and its
"already applied / written but not applied / not started at all"
distinction in section 0 is meant to stay accurate as this document is
updated across future sessions — update that section first whenever
anything here actually gets applied or torn down.
