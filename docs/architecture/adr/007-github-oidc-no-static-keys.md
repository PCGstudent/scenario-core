# ADR-007: GitHub OIDC, no static AWS keys

**Status:** Accepted, implemented in Phase 3a (`infra/terraform/modules/ci_oidc`).

## Context

CI needs to authenticate to AWS to build/push images and apply
Terraform. Long-lived access keys stored as repository secrets are a
standing credential-theft risk with no natural expiry.

## Decision

- One `aws_iam_openid_connect_provider` trusting
  `token.actions.githubusercontent.com`, with `aud = sts.amazonaws.com`.
- The DEV deploy role (`gha-ci-dev`)'s trust policy uses `StringEquals`
  on the **full** `sub` claim
  (`repo:PCGstudent/scenario-core:ref:refs/heads/main`), never
  `StringLike` with a `repo:owner/repo:*` wildcard -- that wildcard would
  let any branch, any fork's pull-request workflow, and any tag assume
  the role.
- PROD's equivalent role (not created by this module -- a separate PROD
  root configuration in the PROD account) pins `sub` to
  `environment:prod` instead, so only a run in the protected GitHub
  `prod` environment (required reviewers) can obtain it.
- No AWS access key exists in this repository's secrets or variables, in
  either account.

## Consequences

- Credentials are short-lived, minted per workflow run, and cannot be
  exfiltrated as a standing secret.
- The trust condition is exactly as narrow as the deployment surface it
  authorises -- a compromised or malicious PR from this repository's own
  main branch is the only thing that could exploit this trust, and that
  already requires write access to main.
- The very first `gha-ci-dev` role does not exist until a human applies
  `envs/dev` once with their own account credentials (see
  `infra/terraform/bootstrap/README.md`) -- CI cannot bootstrap its own
  trust relationship from nothing.

## Alternatives considered

- **Long-lived IAM user access keys as GitHub secrets.** Rejected: no
  natural expiry, easy to leak via logs or a compromised dependency, and
  requires manual rotation discipline that erodes over time.
- **`StringLike` with a wildcarded `sub`.** Rejected: widens the trust
  condition to any branch/fork/tag in the repository, defeating the
  purpose of pinning it at all.
