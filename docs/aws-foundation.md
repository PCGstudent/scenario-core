# Phase 3a: AWS foundation -- deployment summary

Concise reference for what this PR builds, what it costs, and exactly how
to deploy it. Nothing in this document has been run against real AWS in
preparing this PR (see "What has and has not been verified" below) --
`terraform validate`, `tflint`, `checkov` and the custom policy checks in
`tests/test_terraform_policy.py` are static analysis, not a deployment
plan or a deployment.

## Deployment-readiness corrections applied since the first draft

A review pass found five concrete gaps between `gha-ci-dev`'s IAM policy
and what `envs/dev` actually manages, plus one workflow injection risk.
All are fixed, with structural regression tests
(`tests/test_terraform_policy.py`) that would have caught each one:

1. **ECR image layers were unreachable.** The S3 gateway endpoint policy
   only allowed the two project buckets; ECR stores every image layer in
   its own AWS-managed bucket, reached over that same endpoint. Added the
   exact, minimal `s3:GetObject` grant AWS's own ECR documentation
   specifies (`arn:aws:s3:::prod-{region}-starport-layer-bucket/*`).
2. **The permissions boundary would have blocked its own probe role and
   silently denied unrelated IAM calls.** `iam:PermissionsBoundary` was
   applied to `GetRole`/policy reads that do not carry that condition key
   (AWS's own IAM User Guide example places those in a separate,
   unconditioned statement) -- as written, this would have denied those
   calls outright. Separately, the runtime boundary was missing
   `ecr:DescribeRepositories`, which the probe task role's own policy
   grants -- the boundary would have capped it below its own policy,
   failing the connectivity probe's ECR check with `AccessDenied` despite
   looking correctly authorised. Both probe roles were also missing the
   `permissions_boundary` argument entirely, which would have made
   `gha-ci-dev`'s own boundary-conditioned `CreateRole` grant refuse to
   create them at all. Also added: explicit denies preventing `gha-ci-dev`
   from modifying its own policies/trust policy, replacing/removing a
   role's boundary, or rewriting the boundary policy's own content
   (AWS's own example calls this last one "NoBoundaryPolicyEdit"). Also
   corrected an inaccurate claim that EC2 VPC-family *create* actions
   lack resource-level permission support -- verified against AWS's own
   documentation, they don't; only pure `Describe`/`List` actions do.
   Added ECS cluster/task-definition and CloudWatch Logs management for
   the probe resources, which `gha-ci-dev` could not previously touch.
3. **`s3:DeleteObject` was granted across the whole state bucket.**
   Restricted to exactly the `.tflock` lock object HashiCorp's own S3
   native-locking documentation specifies; the state object itself now
   only ever gets `GetObject`/`PutObject`.
4. **The workflow's confirmation input was interpolated directly into
   shell source** -- a documented GitHub Actions script-injection vector.
   Moved through a step `env:` variable instead. `id-token: write` was
   also workflow-wide; it is now job-scoped to the two jobs that actually
   authenticate to AWS.

None of these required a broader service wildcard to fix -- each is a
narrowly scoped addition, restriction, or explicit deny.

## Region and accounts

| | Value | Source |
|---|---|---|
| Region | `eu-west-1` | `docs/architecture/IMPLEMENTATION_PLAN.md` Section 26 ("eu-west-1 only, no DR requirement" assumed); **confirmed**, not merely assumed -- the local `~/.aws/config` profile `4xtra-dev` also names `region = eu-west-1` |
| DEV account | `758895552145` | IMPLEMENTATION_PLAN.md Section 13.4/19; **confirmed** against the local AWS config profile `4xtra-dev` (`login_session = arn:aws:iam::758895552145:user/iamadmin`), which matches the plan's own stated "verified DEV identity" exactly |
| PROD account | `488182246436` | IMPLEMENTATION_PLAN.md Section 19; **confirmed** against the local AWS config profile `4xtra-prod` in the same way. Not deployed to, not touched -- named only in `gha-ci-dev`'s explicit cross-account `Deny` (Section 13.3) |

Both account IDs were independently corroborated from a second source (the
local, pre-existing AWS CLI configuration) rather than taken from the
architecture document alone.

## Resource inventory (this PR, DEV only)

| Module | Resources |
|---|---|
| `bootstrap` | 1 S3 bucket (Terraform state), 1 KMS key + alias |
| `modules/kms` | 1 KMS key + alias (environment CMK) |
| `modules/artifact_store` | 2 S3 buckets (artifacts, runs) + their encryption/public-access-block/lifecycle/policy resources |
| `modules/job_store` | 2 DynamoDB tables (scenario-jobs, model-registry) |
| `modules/worker_image` | 1 ECR repository + lifecycle policy |
| `modules/network` | 1 VPC, 2 private subnets, 1 route table, 2 security groups, 1 locked-down default security group, 2 gateway endpoints (S3, DynamoDB), 3 interface endpoints (ecr.api, ecr.dkr, logs), 1 flow-log S3 bucket + `aws_flow_log` |
| `modules/ci_oidc` | 1 OIDC provider, 1 IAM role (`gha-ci-dev`) + 7 inline policies, 1 permissions-boundary IAM policy |
| `envs/dev/probe.tf` | 1 ECS cluster, 2 IAM roles (probe execution/task), 1 CloudWatch log group, 1 ECS task definition (Phase 3a acceptance tooling -- see below) |

Nothing from Phase 3b (ECS *service*/task launched as a standing
workload, Step Functions, the HTTP API, control-plane Lambdas,
observability alarms) exists in this inventory.

## Expected cost

Standing (fixed) monthly cost, DEV, at rest with no traffic:

| Item | Basis | Monthly |
|---|---|---|
| **VPC interface endpoints** | 3 endpoints x 2 AZ x 730 h x **$0.01/hr per endpoint-AZ** (AWS PrivateLink pricing page, verified directly against the official page at the time of writing -- not the architecture document's own $0.011 estimate, which rounds slightly high) | **~ $43.80** |
| KMS CMKs | 2 keys (bootstrap + environment) x $1.00 | $2.00 |
| S3 storage | Near-empty buckets at this point | < $0.01 |
| S3 + DynamoDB gateway endpoints | Free | $0.00 |
| DynamoDB on-demand + PITR | Two empty tables | < $0.05 |
| ECR storage | No image pushed yet by this PR | $0.00 |
| VPC flow logs to S3 | REJECT only, near-empty | < $0.10 |
| ECS cluster (probe) | No idle cost -- a cluster with no running task/service costs nothing | $0.00 |
| **NAT Gateway / IGW** | **Not created** | **$0.00** |
| **Total, at rest** | | **~ $46/month** |

Usage-dependent cost (not a standing charge, scales with actual use):

- **Endpoint data processing**: $0.01/GB through the interface endpoints
  (image pulls, log delivery) -- negligible until real traffic exists.
- **Connectivity-probe runs**: a single Fargate task at 0.25 vCPU / 0.5 GB
  for a few seconds per run -- well under $0.01 per invocation, run
  on demand, never as a standing service.
- **ECR storage**: once the worker image is pushed (`deploy-dev.yml`'s
  build-and-push job), ~1 GB x up to 5 retained images (lifecycle policy)
  -- on the order of $0.50/month, matching the architecture document's own
  Section 20.1 estimate.

This is close to, and directionally consistent with, IMPLEMENTATION_PLAN.md
Section 20.1's ~$51/month estimate for DEV at light job volume -- the
difference here is that this PR's inventory has no Fargate compute
running jobs yet (that arrives with Phase 3b), so the "at rest" figure
above is lower, and the usage-dependent lines below it are what closes the
gap once jobs start running.

**Interface-endpoint cost is, deliberately, the largest single line** --
consistent with the architecture document's own framing (Section 20.2:
"roughly 90% of the DEV bill is PrivateLink"). It buys the private,
egress-free network path (ADR-010), not compute.

Source for the interface-endpoint rate: [AWS PrivateLink pricing](https://aws.amazon.com/privatelink/pricing/)
("$0.01 per hour for each endpoint ENI... for all AWS Regions"), fetched
directly while preparing this PR.

## Required bootstrap permissions and authentication method

- **Bootstrap** (`infra/terraform/bootstrap`) and the **first**
  `envs/dev` apply must run with direct, authenticated AWS credentials
  for the DEV account (758895552145) -- a human operator, or an
  already-authorized principal. `gha-ci-dev`, the role every subsequent
  CI deployment uses, does not exist until that first apply creates it
  (see `infra/terraform/bootstrap/README.md` for the exact ordering).
- **Every deployment after that** uses `gha-ci-dev`, assumed by
  `deploy-dev.yml` over GitHub OIDC -- no AWS access key anywhere, in
  either account, ever (ADR-007).
- The account holder's existing local AWS CLI profile (`4xtra-dev`) uses
  a session-based login (`login_session = arn:aws:iam::758895552145:user/iamadmin`
  in `~/.aws/config`), not static keys -- consistent with the "move away
  from long-lived keys" direction Section 13.4 already recommends. That
  session had expired at the time this PR was prepared (`aws sts
  get-caller-identity` returned "Your session has expired. Please
  reauthenticate using 'aws login'" for that profile); no attempt was
  made to reauthenticate or otherwise act as that identity.

## Exact first-deployment sequence

See `infra/terraform/bootstrap/README.md` for the full, step-by-step
version. Summary:

1. `cd infra/terraform/bootstrap && terraform init && terraform apply
   -var environment=dev -var owner=<you>` -- creates the state bucket and
   bootstrap CMK, with local state.
2. `cd infra/terraform/envs/dev && terraform init && terraform plan
   -out=dev.tfplan && terraform apply dev.tfplan` -- using the SAME
   direct account credentials as step 1 (not OIDC -- `gha-ci-dev` doesn't
   exist yet). Creates everything in the resource inventory above.
3. Record `terraform output gha_ci_dev_role_arn` and
   `terraform output ecr_repository_url` (or run `terraform output` for
   everything). Set them as GitHub Actions repository **variables**
   (Settings -> Secrets and variables -> Actions -> Variables, not
   Secrets -- these are ARNs and a repository name, not credentials):
   - `GHA_CI_DEV_ROLE_ARN` = the role ARN from step 3
   - `ECR_REPOSITORY_NAME` = `4xtra-dev-worker`
4. Build and push the connectivity-probe image once, manually, using the
   same credentials as steps 1-2 (this is acceptance tooling, not part of
   the CI build-once/promote-by-digest pipeline):
   ```
   aws ecr get-login-password --region eu-west-1 | docker login --username AWS --password-stdin <account>.dkr.ecr.eu-west-1.amazonaws.com
   docker build --platform linux/amd64 -f docker/probe.Dockerfile -t <repo_url>:probe .
   docker push <repo_url>:probe
   ```
5. From then on, `deploy-dev.yml` is triggered manually via
   `workflow_dispatch` (type `deploy` to confirm) for any further build/
   push/apply -- never automatically on a push to `main` (see that
   workflow's own header comment for why).

## Connectivity probe

`scripts/connectivity_probe.py`, run as the one-off ECS Fargate task
`infra/terraform/envs/dev/probe.tf` defines, checked into the private
subnets created above:

```bash
aws ecs run-task \
  --cluster 4xtra-dev-probe \
  --task-definition 4xtra-dev-connectivity-probe \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[<private-subnet-ids>],securityGroups=[<task-sg-id>],assignPublicIp=DISABLED}" \
  --region eu-west-1
```

(Subnet/security-group IDs come from `terraform output private_subnet_ids`
/ `terraform output task_security_group_id`.) Then read the result from
CloudWatch Logs, log group `/4xtra/dev/probe` -- the task prints one JSON
report line and exits non-zero if any check failed. **Cleanup**: nothing
to clean up between runs -- `RunTask` tasks are not standing services and
stop on their own after the container exits; the cluster and task
definition are free to leave registered indefinitely.

**This has not been run.** Preparing it (the script, the IAM roles scoped
to exactly what it calls, the task definition, this exact command) is
everything achievable without AWS access; running it and reporting a
result is the next actionable step once the sequence above has deployed
the network it tests.

## Drift / no-op / destroy-recreate verification (procedures, not run)

- **No-op re-apply**: `cd infra/terraform/envs/dev && terraform plan`
  immediately after a successful `apply` should report `No changes.` If
  it does not, that is drift worth investigating before trusting the
  state file further.
- **Destroy/recreate**: `terraform plan -destroy` shows exactly what
  would be removed without removing it. A real destroy/recreate cycle
  should only ever be run against a disposable DEV environment that holds
  nothing of value, and never against `bootstrap` (its state bucket
  carries `prevent_destroy = true` specifically so an accidental destroy
  cannot succeed).
- **None of the above has been run against real AWS in preparing this
  PR** -- no `terraform apply`, `terraform destroy`, or any AWS
  credential use beyond the read-only identity check described above.

## What has and has not been verified

**Verified in this PR** (all runnable without AWS credentials):
`terraform fmt -check`, `terraform init -backend=false`,
`terraform validate` (every module and both roots), `tflint`, `checkov`
(0 failed checks across the whole tree, every skip justified inline),
the custom policy checks in `tests/test_terraform_policy.py` (including
negative-control tests proving each check can actually fail), and the
full existing repository test/lint/type/audit suite (unaffected by this
PR -- see the PR body for exact counts).

**NOT verified, and not claimed to be**: that `terraform apply` actually
succeeds against real AWS; that the connectivity probe actually passes;
that the measured cost matches this estimate within Phase 3a's own
acceptance criterion 4 (~20%); that GitHub Actions' OIDC exchange
actually works end-to-end from a real workflow run (the already-diagnosed
`startup_failure` condition on this repository's self-hosted runner is
out of scope here, not investigated, and not worked around -- if it
blocks `deploy-dev.yml` from running at all, that is a blocked gate to
report, not something a manual SSO-authenticated deployment could stand
in as proof of). `terraform validate` is not a deployment plan, and a
plan was not produced here because no authenticated AWS session was
available (see above).

## Remaining inputs needed from the user

1. **Run the first-deployment sequence above** (bootstrap, then the
   first `envs/dev` apply) with direct DEV-account credentials -- this PR
   cannot and does not do that.
2. **Set the two GitHub Actions repository variables**
   (`GHA_CI_DEV_ROLE_ARN`, `ECR_REPOSITORY_NAME`) once step 2 above has
   produced real values.
3. **Push the connectivity-probe image once**, manually (step 4 above).
4. **Decide whether/when to enable automatic deployment on push to
   `main`** -- `deploy-dev.yml` is `workflow_dispatch`-only by design in
   this PR; switching that is a one-line, reviewable follow-up once the
   team is ready to accept it.
5. **Report back the actual `terraform apply` result, the connectivity
   probe's output, and the measured monthly cost** once run -- none of
   the three can be honestly claimed from this execution.
