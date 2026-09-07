# Phase 3b deployment summary: first complete vertical slice

Companion to `docs/aws-foundation.md` (Phase 3a), in the same spirit: an
honest account of what this PR builds, what has been verified locally,
and what only a real AWS deployment can verify. **No `terraform apply`,
`terraform plan` against the real backend, ECR push, or task/job
submission was performed while preparing this PR** — everything below is
either static analysis (`terraform validate`, `ruff`, `mypy`), or a
`pytest` run against `moto`-mocked AWS services, never a real account.

## What this PR adds

Architecture plan Section 24 Phase 3b, Section 25's "recommended first
vertical slice": API Gateway (`AWS_IAM` auth) → a thin submit Lambda →
Step Functions Standard → ECS Fargate (the existing Phase 2 worker image,
extended) → S3 + DynamoDB, run against one pre-registered, manually
approved artifact (Phase 4's calibration/promotion workflow does not
exist yet, by design — see Section 25's own scope).

| Area | What was built |
|---|---|
| Terraform | `modules/{worker_compute, job_orchestrator, job_api, observability}`, wired into `envs/dev/main.tf`; `modules/demo_killswitch` (standalone, not wired into `envs/dev` — see its own header comment) |
| State machine | `modules/job_orchestrator/state_machine.asl.json.tftpl` — `RecordQueued → RunSimulation (ecs:runTask.sync) → [success] RecordSucceeded` / `[Catch: States.ALL] → ClassifyFailure → RecordAttempt → RetryDecision (Choice, bounded by MAX_ATTEMPTS) → IncrementAttempt/WaitBeforeRetry → RunSimulation` or `→ RecordFailed` |
| Control plane | `src/scenario_platform/control/{schemas,hashing,admission,errors,submit,jobs,classify_failure}.py` |
| Data-plane adapters | `src/scenario_platform/adapters/{job_store,s3_store}.py` — "the ONLY place boto3 appears in the data plane" (Section 22); `scenario_platform.domain`/`worker` remain boto3-free, enforced by `tests/test_no_aws_in_core.py` |
| Worker extension | `simulate --job-id JOB_ID` mode on the existing CLI (`worker/__main__.py`) — reads the job document from DynamoDB and the artifact from S3, publishes to `runs/{job_id}/` in S3, never imports boto3 directly (calls the adapters instead) |
| Scripts | `scripts/seed_registry.py` (manual artifact pre-registration), `scripts/smoke.py` (post-deploy, AWS-only), `scripts/package_control_plane.py` (Lambda zip build) |
| Tests | `tests/scenario_platform/test_control_{hashing,admission,submit,jobs}.py`, `test_classify_failure.py`, `test_worker_job_id_mode.py`, `tests/infra/test_state_machine_definition.py` |

## Contracts implemented, and where they diverge from the plan's own text

- **Idempotency** (Section 6.2/6.2a): one `TransactWriteItems` for
  `IDEM#`+`JOB#`, hash over caller-provided fields only, healing via a
  deterministically **constructed** execution ARN rather than the plan's
  suggested `DescribeExecution`/`ListExecutions` recovery — `submit.py`'s
  own docstring explains why the construction is a strictly more
  reliable simplification, not a deviation in outcome.
- **Approval predicate** (Section 8.4): `CANDIDATE#`/`APPROVAL#`/
  `POINTER#` read exactly as specified; write-side enforcement
  (calibrator vs. approver role separation) is Phase 4 and not built —
  `scripts/seed_registry.py` writes all three item kinds directly, using
  direct human credentials, exactly like `bootstrap`'s own first-apply
  posture.
- **Failure classification** (Section 6.3): no declarative `Retry` on
  `RunSimulation`; `Catch: States.ALL` → `ClassifyFailure` → bounded
  `RetryDecision` loop. Structural test
  (`tests/infra/test_state_machine_definition.py`) asserts the shape,
  never a speculative ECS error name, per the plan's own warning.
- **Task timeout** (Section 6.1 step 3): a **static** `task_timeout_seconds`
  (default 600s), not the plan's fully admission-derived per-request
  value — computing the latter needs a DynamoDB read of the job's own
  `timeout_s` before `RunSimulation`, since the execution input is
  deliberately just `{job_id}` (Section 6.2a). `admission.MAX_PATH_YEARS`
  (300,000, chosen to comfortably admit the plan's own canonical
  1,000-path/252-day acceptance job) is sized so its worst case stays
  under the static timeout with margin — documented in both
  `admission.py` and `modules/job_orchestrator/variables.tf`.
- **`{env}-api-registry`** (list model versions, Section 13.1's IAM
  table): not built. Section 25's own "recommended first vertical slice"
  names only submit/status/classify_failure; registry listing is a
  disclosed Phase 4+-adjacent omission, not a missing IAM row by
  accident.
- **Worker write scope** (Section 13.1's "honest limitation"):
  `s3:PutObject` on `runs/*`, not `runs/{job_id}/*` — unchanged from the
  plan's own disclosed limitation; Phase 5's STS delegate-role narrowing
  is not attempted here.

## Verified locally (pytest, `moto`, no AWS account touched)

- Idempotency matrix: new job vs. replay (matching/differing hash),
  no-key creates distinct jobs, healing a `SUBMITTED`-with-no-`execution_arn`
  job, **replay surviving a `POINTER#` move** (Section 24's own named
  test case).
- Admission (`MAX_HORIZON`/`MAX_PATH_YEARS`) and policy (restricted tail
  level with/without governance) rejections, both as 422s.
- Approval predicate: missing candidate, missing/mismatched approval,
  `POINTER#` naming an artifact inconsistent with its own candidate.
- `ClassifyFailure`'s full table: deterministic exit codes (INPUT,
  ARTIFACT_INTEGRITY), OOM (exit 137 and `stoppedReason` text),
  `TaskFailedToStart`, `AmazonECS.Unknown`, first-vs-second image-pull
  failure (TRANSIENT_INFRA vs. CONFIG), `States.Timeout`, and the
  UNCLASSIFIED fallback never retrying.
- State-machine structure: no `Retry` on `RunSimulation`, `Catch` shape,
  bounded `RetryDecision`, terminal states.
- The worker's `--job-id` path: downloads the **exact same committed
  golden fixture** (`tests/fixtures/gjr_skewt_v1/`) via mocked S3, reads
  its job document via mocked DynamoDB (including the `Decimal`
  round-trip real DynamoDB imposes — `job_store.to_native`/`to_decimal`),
  and publishes a result whose array digest is compared, in-process,
  against a fresh local `domain.services.simulate` call over the same
  artifact+request (not against the historical hard-coded golden digest
  — this environment's own `tests/test_golden_fixture.py` already fails
  that exact comparison, a pre-existing BLAS/environment drift unrelated
  to this PR; see "Known pre-existing failure" below).
- `terraform validate` (envs/dev with the new modules; `demo_killswitch`
  standalone) and the full custom Terraform policy suite
  (`tests/test_terraform_policy.py`) — no NAT/IGW, no `Action:"*"`, every
  `Resource:"*"` allowlisted in `infra/terraform/policy/resource-star-allowlist.yaml`
  with an individually-verified justification.
- `ruff check`/`ruff format --check` and `mypy --strict` clean on every
  new/changed file under `src/scenario_platform`.
- Full existing repository test suite: 381 passed, 4 skipped
  (Docker-gated), 4 failed — all 4 the same pre-existing, environment-
  dependent digest mismatch described below, reproduced on code this PR
  never touches.

### Known pre-existing failure, not caused by this PR

`tests/test_golden_fixture.py::test_simulating_from_the_committed_fixture_reproduces_the_golden_digest`
and three tests in `tests/test_replay.py` fail in this execution
environment, calling only pre-existing `domain.services.simulate`/
`worker/__main__.py` code this PR does not modify. `docker/worker.
Dockerfile`'s own Phase 2 acceptance-criteria section already names this
exact risk ("Base-image BLAS and SIMD differences... where the README's
documented fit drift becomes visible"). Confirmed by inspection: the failing test calls only
`domain.services.simulate`/`domain.serialization.load_artifact`, neither
of which this PR modifies (`worker/__main__.py`'s changes are additive —
a new `--job-id` code path — and never touch `_reconstruct_generator` or
any existing function this test exercises).
`tests/scenario_platform/test_worker_job_id_mode.py`'s own
digest test was written to compare against a same-process local
computation specifically to stay independent of this drift, rather than
inheriting the historical hard-coded digest.

## What only a real AWS deployment can verify (cannot be checked here)

1. `terraform apply` for `envs/dev` + `demo_killswitch` actually
   succeeding — IAM `PassRole`/`RunTask` condition debugging is Section
   24's own named risk for this phase, and `terraform validate` does not
   catch a runtime `AccessDenied`.
2. A real Fargate task producing a **byte-identical** array to the local
   golden fixture (Phase 3b acceptance criterion 2) — proves Tier-1
   reproducibility survives the real container runtime, not just the
   code path (which is what the moto-backed test above actually proves).
3. The manifest recording a real Fargate task ARN, and `TaskStartSeconds`/
   `ComputeSeconds` being visible as CloudWatch metrics (acceptance
   criteria 3 and 5).
4. Control-plane Lambda deployed package size staying under 15 MB
   (acceptance criterion 4) — depends on `scripts/package_control_plane.py`'s
   real output on a Linux/manylinux target, not measurable from this
   Windows development environment.
5. The demo-cleanup Lambda's `ec2:DeleteVpcEndpoints`/`ecs:StopTask`
   calls succeeding against real endpoint/task state transitions — moto's
   EC2/ECS mocks do not model deletion timing with full fidelity.
6. `scripts/smoke.py` end to end (real SigV4-signed requests against a
   real API Gateway `AWS_IAM` authorizer) — the unauthenticated-request
   rejection is primarily an API-Gateway-configuration property, and the
   Lambda-level check in `control/errors.py::principal_arn` is
   defense-in-depth, not the primary proof.
7. Real measured cost against `docs/aws-demo-runbook.md`'s estimate
   (Cost Explorer is disabled on this account as of the prior turn).

`docs/aws-demo-runbook.md` sequences items 1-2 and 6-7 into a single
bounded demo window with an independent auto-cleanup; items 3-5 are
observable from the same window's `terraform output`/CloudWatch/ECR
console without extra steps.

## Branch and review

Prepared on `feat/aws-phase3b`, opened as a PR against `main`, **not
merged automatically** — per this turn's explicit instruction, merging
is a separate, later decision.
