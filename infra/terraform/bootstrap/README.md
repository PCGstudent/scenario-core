# Bootstrap ordering

This directory breaks the chicken-and-egg problem every remote-state design
has: Terraform cannot start from a remote backend that does not exist yet,
and it cannot create the GitHub OIDC role CI will use to authenticate before
*something* has authenticated to create that role in the first place. Both
gaps are closed by one, explicit, human-run, once-per-account sequence.

## The exact first-time sequence, per AWS account

Run once per account (DEV now; PROD later, in the PROD account, by someone
authorized there). All steps below use the existing human identity for that
account (`arn:aws:iam::758895552145:user/iamadmin` for DEV, confirmed in
`~/.aws/config` locally as profile `4xtra-dev`) — **never** GitHub OIDC,
because the OIDC role does not exist until step 3 creates it.

1. **Bootstrap, with local state.**
   ```bash
   cd infra/terraform/bootstrap
   terraform init                       # local state -- see backend.tf
   terraform apply -var environment=dev -var owner=<your-name-or-team>
   ```
   Creates: the `4xtra-dev-tfstate-<account_id>` S3 bucket (versioned,
   SSE-KMS, public access blocked) and the bootstrap-only CMK that encrypts
   it. Nothing else. This state file stays local to the machine that ran
   it — it holds no secrets, only resource IDs, and is easy to back up out
   of band. Record `terraform output state_bucket_name` for step 2.

2. **Point envs/dev at the real backend.**
   `envs/dev/backend.tf` already names the expected bucket
   (`4xtra-dev-tfstate-758895552145`) and `use_lockfile = true` (S3 native
   locking, Terraform 1.10+ — no DynamoDB lock table). If step 1 produced a
   different bucket name (a different account), update it there before
   proceeding.

3. **First envs/dev apply — human credentials, not OIDC.**
   ```bash
   cd infra/terraform/envs/dev
   terraform init
   terraform plan  -var-file=terraform.tfvars -out=dev.tfplan
   terraform apply dev.tfplan
   ```
   This single apply creates *everything* in Scope for Phase 3a, including
   the `ci_oidc` module's GitHub OIDC provider and the `gha-ci-dev` role
   whose trust policy names this repository's `main` branch. It must be run
   by a human (or an already-authorized principal) using the account's own
   credentials, because the very role that would let CI do this does not
   exist until this apply finishes creating it.

4. **From this point on, CI does the deploying.** Every subsequent
   `deploy-dev.yml` run assumes `gha-ci-dev` over OIDC — no AWS access key
   anywhere, ever, in CI. A human only needs to fall back to step 3's
   direct-credential path again if the OIDC provider or role itself is ever
   deleted or needs disaster recovery.

## Why bootstrap's own state stays local, not self-hosted

See the comment in `backend.tf`. Short version: bootstrap's resource count
is small and nearly static (one bucket, one key), so the operational
simplicity of local state outweighs the appeal of self-hosting it in the
bucket it creates — a design this document is honest about rather than
building a second migration step that mostly exists to look consistent.

## Not run in this delivery

No `terraform apply` (bootstrap or envs/dev) has been run as part of
preparing this PR — no AWS credentials were available/authenticated in this
execution (see the PR's deployment-summary section). This README is the
exact, actionable sequence for whoever runs it first, not a record that it
has already happened.
