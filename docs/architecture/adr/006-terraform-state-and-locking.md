# ADR-006: Terraform with account-level DEV/PROD isolation and S3 native state locking

**Status:** Accepted, implemented in Phase 3a (`infra/terraform/bootstrap`, `infra/terraform/envs/dev/backend.tf`).

## Context

Terraform needs remote state (shared, lockable, encrypted) and an
isolation model between DEV and PROD that cannot leak by accident.

## Decision

- **Account-level isolation, never a Terraform workspace.** DEV
  (758895552145) and PROD (488182246436) are separate AWS accounts, each
  with its own state bucket, own bootstrap CMK and own environment CMK.
  Workspaces share a state bucket and (typically) a credential set --
  exactly the isolation this design must not have.
- **S3 native state locking** (`use_lockfile = true`, Terraform 1.10+):
  no DynamoDB lock table. One fewer resource, and the DynamoDB locking
  mechanism is deprecated upstream.
- **A one-time bootstrap, applied with local state**, creates the state
  bucket and its own encryption key before any environment's remote
  backend can exist -- documented explicitly in
  `infra/terraform/bootstrap/README.md`, including why bootstrap's own
  state stays local rather than self-hosting.

## Consequences

- No cross-environment blast radius through shared state.
- Bootstrap is the one piece of infrastructure a human must apply with
  direct account credentials, once per account -- not a limitation to
  work around, a chicken-and-egg problem solved explicitly rather than
  hidden.
- PROD's equivalent bootstrap + `envs/prod` root configuration is future
  work in its own account, not built by this ADR.

## Alternatives considered

- **Terraform Cloud/HCP remote state.** Rejected for this phase: adds an
  external SaaS dependency and its own auth model for no benefit over
  S3+native-locking, which is already inside the AWS account boundary
  this design is built around.
- **DynamoDB lock table.** Rejected: an extra resource for a mechanism
  Terraform itself is deprecating in favour of S3 native locking.
- **Terraform workspaces for DEV/PROD.** Rejected: shares state and
  credentials across environments, which is precisely the isolation this
  design requires.
