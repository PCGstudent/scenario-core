# ADR-024: Realistic IAM wildcard policy

**Status:** Accepted, implemented in Phase 3a (`infra/terraform/modules/ci_oidc`, `infra/terraform/policy/resource-star-allowlist.yaml`, `tests/test_terraform_policy.py`).

## Context

An earlier revision of the design claimed "no `Resource: "*"` except
`ecr:GetAuthorizationToken`." That absolute cannot survive contact with a
real deployment: many AWS `Describe*`/`List*` actions and several
creation APIs (`kms:CreateKey`, most of the EC2 VPC-family surface) do
not support resource-level permissions at all, and an
infrastructure-creating identity necessarily has a broader surface than a
runtime identity -- it creates the very ARNs a resource-scoped policy
would otherwise have to name in advance.

## Decision

1. **No `Action: "*"`,** anywhere, in any role.
2. **No broad service wildcards** (`s3:*`, `iam:*`, `dynamodb:*`,
   `ecs:*`, `kms:*`, `logs:*`) in any Allow statement of any IAM identity
   policy, runtime or deployment. (A wildcard action inside a *Deny*
   statement -- e.g. the deploy role's `iam:*` guardrail outside the
   project prefix -- is hardening, not escalation, and is exempt.)
3. **Runtime roles are resource-scoped wherever AWS supports
   resource-level permissions**, narrowed further with conditions
   (prefix, key-space, service) where they materially reduce the
   surface.
4. **`Resource: "*"` is permitted only for actions on an explicit
   allow-list** (`infra/terraform/policy/resource-star-allowlist.yaml`),
   each entry naming the role it applies to and a justification a
   reviewer can check against the AWS service-authorization reference.
5. **Deployment roles are reviewed separately from runtime roles**,
   against a different standard, because infrastructure creation
   legitimately needs a wider surface -- reduced instead by naming
   constraints (`4xtra-{env}-*`), a permissions boundary attached to
   every role the deploy role can create, an explicit `Deny` on `iam:*`
   outside the project prefix, an explicit `Deny` on cross-account
   `sts:AssumeRole`, and a `Deny` on deleting the state bucket, the CMKs
   and the artifacts bucket.
6. Every rule above is checked structurally by
   `tests/test_terraform_policy.py`, not left to manual review alone --
   including negative-control tests proving each assertion actually
   rejects a violation, not just passes against already-compliant source.

## Consequences

- The policy states what deployment infrastructure-creation actually
  requires, rather than an aspiration the pipeline would immediately have
  to violate.
- Every `Resource: "*"` grant is a reviewed, visible diff (a new
  allow-list entry), not a silent widening.
- The permissions-boundary condition on `iam:CreateRole` means a
  compromised deploy role cannot mint a role more privileged than the
  boundary allows, even though the deploy role's own surface is broad.

## Alternatives considered

- **Keep the absolute "no `Resource: "*"` except one action" rule.**
  Rejected: false on its face against real AWS API constraints (EC2
  VPC-family resource-level permissions, `kms:CreateKey`), which would
  force either an unenforceable policy or silently granting broader
  wildcards without the allow-list's reviewability.
- **One combined allow-list for runtime and deployment roles.** Rejected:
  conflates two different risk postures -- a compromised runtime role and
  a compromised deploy role have very different blast radii, and
  reviewing them against the same standard would either be too strict for
  deployment or too loose for runtime.
