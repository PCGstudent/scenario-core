# ADR-010: Private subnets, no NAT and no internet gateway; identical DEV/PROD topology differing only in AZ count

**Status:** Accepted, implemented in Phase 3a (`infra/terraform/modules/network`).

## Context

The worker's dependencies are baked into its image; its data comes from
S3, its results go to S3/DynamoDB, its logs go to CloudWatch. It has no
legitimate reason to reach the public internet at all -- and market-data
acquisition at run time is already forbidden for correctness reasons
(datasets are calibration-time snapshots, not run-time fetches).

## Decision

- Private subnets only. **No `aws_internet_gateway`, no
  `aws_nat_gateway`, anywhere, in any environment.** Structurally
  enforced, not just by omission: `tests/test_terraform_policy.py`
  scans every `.tf` file in `infra/terraform` for either resource type.
- Exactly five VPC endpoints: S3 + DynamoDB gateway endpoints (free), and
  three interface endpoints -- `ecr.api`, `ecr.dkr`, `logs`. Each carries
  an endpoint policy restricting it to this project's own
  buckets/tables/repository (or, for `logs`, this AWS account) --
  a second, independent authorisation layer beneath IAM.
- Two security groups: `endpoints` (accepts HTTPS from the task SG only)
  and `task` (no ingress at all; egress limited to HTTPS toward the
  endpoints SG and the two gateway-endpoint prefix lists).
- **Identical topology in DEV and PROD.** The only difference is AZ
  count (`subnet_count`: 2 in DEV, 3 in PROD) -- a sizing parameter, not
  a topology difference, so nothing can work in DEV and fail in PROD
  because of a network-shape divergence.

## Consequences

- No route to the internet exists to misconfigure -- a stronger,
  structurally-verifiable property than "egress is restricted by policy."
- DEV's three interface endpoints across two AZs cost roughly $48/month,
  which is *more* than a single NAT gateway (~$33/month) at this scale --
  stated honestly (§14.2 of the implementation plan) rather than
  overclaiming that endpoints are always cheaper. The decision is made on
  security and DEV/PROD parity grounds, not price.
- A future genuinely-external dependency (e.g. an AI feature that must
  call a third-party API) gets a dedicated, minimal, explicitly-approved
  egress path for that one function -- never a NAT gateway reopening the
  whole VPC.

## Alternatives considered

- **NAT gateway.** Rejected: grants the data plane unrestricted outbound
  reachability to the entire internet, for a component that has nothing
  legitimate to reach out there, and image-layer traffic over a NAT would
  be metered ($0.045/GB) instead of free (the S3 gateway endpoint).
- **Public-subnet Fargate with public IPs in DEV, private in PROD.**
  Rejected on parity grounds before cost is even considered -- it changes
  the routing model, security-group semantics and failure modes, so DEV
  would systematically fail to exercise PROD's actual network path.
- **DEV endpoints in a single AZ (~$24/month).** A recorded, reversible
  saving if DEV's $48 is ever judged not worth it -- changes only AZ
  multiplicity, no topology/routing/policy/IAM difference. Not adopted
  here because Phase 3a's acceptance criteria assume 2 AZs.
