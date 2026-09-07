# Phase 3b deployment summary: first complete vertical slice

Companion to `docs/aws-foundation.md` (Phase 3a), in the same spirit: an
honest account of what this PR builds, what has been verified locally,
and what only a real AWS deployment can verify. **No `terraform apply`,
`terraform plan` against the real backend, ECR push, or task/job
submission was performed while preparing this PR** — everything below is
either static analysis (`terraform validate`, `ruff`, `mypy`), a `pytest`
run against `moto`-mocked AWS services, or a real local Docker build/run
of the worker image (never a real AWS account).

## Corrections made after an independent review of the initial PR

A review of the first version of this PR found six real defects, fixed in
this revision:

1. **The Lambda deployment zip transitively pulled in the full scientific
   stack.** `control.admission` imports `domain.policies`, which had an
   unconditional module-level `from .artifacts import StructuralDiagnostics`
   — and `domain.artifacts` imports numpy/pandas/`xtra_takehome.challenger`.
   Fixed by moving that import behind `TYPE_CHECKING` (the same pattern
   `domain/identity.py` already used for its own circular-import
   avoidance), since nothing in `policies.py` needs the class itself at
   runtime, only its two attributes. **Proven, not just fixed**: built the
   real zip (`scripts/package_control_plane.py`) and ran all three
   handlers inside the actual `public.ecr.aws/lambda/python:3.13` base
   image, importing ONLY the zip's own extracted content — 2.6 MB, boto3
   resolved from the Lambda runtime itself (confirmed via
   `boto3.__file__`), zero scientific modules in `sys.modules` afterward.
   The build script now also excludes boto3/botocore from the zip
   entirely (the Lambda runtime already provides them; bundling them too
   produced a 21.3 MB zip, over budget on its own) and runs this same
   clean-room check automatically as part of `build()`, not as a one-off
   manual step.
2. **Two DynamoDB status writes could regress an already-advanced job.**
   `set_execution_arn` wrote `status=QUEUED` unconditionally alongside the
   ARN, which could stomp a `RUNNING`/`SUCCEEDED`/`FAILED` value the state
   machine's own `RecordQueued`/`RecordSucceeded` had already written by
   the time this synchronous caller got there; `mark_cancelled` and the
   ASL's own `RecordSucceeded`/`RecordFailed` writes were similarly
   unconditional. Fixed with forward-only conditional writes throughout
   (`job_store._conditional_update`, plus `ConditionExpression`s and a new
   `AlreadyCancelled` terminal state in the ASL) and a corrected
   `get_job`/`get_idem_record` — the former's docstring had the
   `ConsistentRead` default backwards. New tests exercise the four named
   interleavings directly: execution advancing before the ARN is
   persisted, cancellation during startup (which now also stops the
   just-started execution), concurrent completion vs. cancellation
   (proven against real DynamoDB condition-expression semantics via
   moto), and replay after completion.
3. **The demo-cleanup Lambda had six real gaps.** Unpaginated
   `ecs:ListTasks`, `StopTask` with no confirmation, no way to stop new
   submissions from placing compute during cleanup, `"deleting"` treated
   as already-gone, a batched `DescribeVpcEndpoints` call that would mask
   every other endpoint's state behind one `NotFound`, and an ignored
   `Unsuccessful` list from `DeleteVpcEndpoints`. All six fixed: task
   definition deregistration now runs first (blocking new `RunTask`
   placements at the ECS API level, not just at the Step Functions retry
   layer), tasks are stopped and confirmed via the `tasks_stopped` waiter,
   endpoints are queried and verified individually, and `Unsuccessful`
   entries are inspected. `envs/dev` now wires `modules/demo_killswitch`
   in CONDITIONALLY (`var.demo_killswitch_enabled`, default `false`)
   rather than as a separate, later apply — the original design's
   "applied alongside" language concealed a real unprotected window
   between two applies, not just a partial-apply failure within one.
   `docs/aws-demo-runbook.md` section 3.1 documents the residual window
   that remains (an apply failing partway through) as an explicit,
   disclosed process discipline, not something Terraform can make atomic.
4. **`upload_run_outputs` had no real protection against a retried
   invocation's manifest overwriting an already-complete result.**
   Manifest-last ordering alone does not stop a second invocation's
   `PutObject` calls from interleaving with the first's. Fixed using S3's
   own conditional write (`IfNoneMatch: "*"` on the manifest key only) —
   confirmed directly that moto enforces this (a second write is rejected
   with `PreconditionFailed`) — with the loser raising
   `ConcurrentPublishSuperseded`, caught by the worker and logged as a
   benign outcome (Section 6.3: a retry is deterministic, so the
   superseded copy's own data files are content-identical to the winner's
   anyway).
5. **A missing artifact, a permissions/config error, and a transient S3
   blip were all classified identically as `ARTIFACT_INTEGRITY`.** Fixed:
   `s3_store.download_artifact` now classifies botocore errors into three
   distinct exceptions (`ArtifactNotFound`, `ArtifactAccessConfigError`,
   `ArtifactTransientError`), preserving the original cause via `from exc`
   throughout. The real, testable effect on retry behaviour: transient-shaped
   errors (`SlowDown`, `RequestTimeout`, connection errors) are now
   retried internally, with a bounded backoff, before giving up — proven
   with a scripted fake client asserting exact call counts for "not
   found" (1 attempt), "access denied" (1 attempt) and "transient,
   eventually succeeds" (retried, then succeeds) cases.
6. **The task definition referenced a mutable `:latest` tag, and the
   worker image regressed over the 700 MB acceptance ceiling once boto3
   was added.** Fixed: `worker_compute` now requires an immutable
   `worker_image_digest` (`@sha256:...`) with no default, and
   `docker/worker.Dockerfile` prunes botocore's per-service data
   directories (432 services shipped, only `s3`/`dynamodb` ever used —
   grepped, not assumed) down to the two actually needed, in the SAME
   `RUN` layer as the `pip install` (a later, separate `RUN` was tried
   first and measured NO size reduction at all, since Docker layers are
   additive — deleting a file in a later layer does not remove its bytes
   from the image). **Ran the full mandatory container acceptance suite
   for real** (`scripts/run_container_acceptance.sh`, fixing three
   Windows/Git-Bash-specific path-handling bugs in the script along the
   way, unrelated to the image itself): all 10 required tests pass, zero
   skipped, image size **682 MB** (was 722 MB before the prune), and the
   golden replay **reproduces the exact historical digest**
   (`sha256:637920e5...fb3c255`) inside the real Linux container.
   **Empirically checked, not assumed**: the four locally-failing digest
   tests were also run, unmodified, against `main` in a disposable `git
   worktree`, in this same Windows environment — they fail identically
   there too, confirming the drift is this Windows Python installation's
   own BLAS build, pre-existing and unrelated to this PR, and — now
   directly demonstrated — absent entirely in the actual Linux deployment
   target.

## What this PR adds

Architecture plan Section 24 Phase 3b, Section 25's "recommended first
vertical slice": API Gateway (`AWS_IAM` auth) → a thin submit Lambda →
Step Functions Standard → ECS Fargate (the existing Phase 2 worker image,
extended) → S3 + DynamoDB, run against one pre-registered, manually
approved artifact (Phase 4's calibration/promotion workflow does not
exist yet, by design — see Section 25's own scope).

| Area | What was built |
|---|---|
| Terraform | `modules/{worker_compute, job_orchestrator, job_api, observability}`, wired into `envs/dev/main.tf`; `modules/demo_killswitch` wired in CONDITIONALLY (`var.demo_killswitch_enabled`, default `false`) so a demo apply creates it atomically alongside `module.network`'s endpoints, never as a later, separate apply |
| Worker image | `docker/worker.Dockerfile` now installs boto3/botocore (pruned to only `s3`/`dynamodb` service data, in the same layer as the install) for the `--job-id` mode below; `worker_compute` now requires an immutable `worker_image_digest`, never a mutable tag |
| State machine | `modules/job_orchestrator/state_machine.asl.json.tftpl` — `RecordQueued → RunSimulation (ecs:runTask.sync) → [success] RecordSucceeded` / `[Catch: States.ALL] → ClassifyFailure → RecordAttempt → RetryDecision (Choice, bounded by MAX_ATTEMPTS) → IncrementAttempt/WaitBeforeRetry → RunSimulation` or `→ RecordFailed` |
| Control plane | `src/scenario_platform/control/{schemas,hashing,admission,errors,submit,jobs,classify_failure}.py` |
| Data-plane adapters | `src/scenario_platform/adapters/{job_store,s3_store}.py` — "the ONLY place boto3 appears in the data plane" (Section 22); `scenario_platform.domain`/`worker` remain boto3-free, enforced by `tests/test_no_aws_in_core.py` |
| Worker extension | `simulate --job-id JOB_ID` mode on the existing CLI (`worker/__main__.py`) — reads the job document from DynamoDB and the artifact from S3, publishes to `runs/{job_id}/` in S3, never imports boto3 directly (calls the adapters instead) |
| Scripts | `scripts/seed_registry.py` (manual artifact pre-registration), `scripts/smoke.py` (post-deploy, AWS-only), `scripts/package_control_plane.py` (Lambda zip build) |
| Tests | `tests/scenario_platform/test_control_{hashing,admission,submit,jobs}.py`, `test_classify_failure.py`, `test_worker_job_id_mode.py`, `test_s3_store.py`, `tests/infra/{test_state_machine_definition,test_demo_killswitch_cleanup}.py` |

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
  that exact comparison on Windows; see "Known pre-existing failure"
  below, now with real-container confirmation that the drift is
  Windows-specific).
- **The real Docker container acceptance suite**
  (`scripts/run_container_acceptance.sh`), against the actual built
  image: all 10 required tests pass, 0 skipped, including the golden
  replay reproducing the exact historical digest inside Linux and the
  700 MB size ceiling. See "Corrections" item 6 above.
- `terraform validate` (envs/dev with the new modules; `demo_killswitch`
  standalone) and the full custom Terraform policy suite
  (`tests/test_terraform_policy.py`) — no NAT/IGW, no `Action:"*"`, every
  `Resource:"*"` allowlisted in `infra/terraform/policy/resource-star-allowlist.yaml`
  with an individually-verified justification.
- `ruff check`/`ruff format --check` and `mypy --strict` clean on every
  new/changed file under `src/scenario_platform`.
- Full existing repository test suite: 407 passed, 4 skipped
  (Docker-gated — separately run for real, see above), 4 failed — all 4
  the same pre-existing, environment-dependent digest mismatch described
  below, confirmed also present on `main` (see "Known pre-existing
  failure") and confirmed ABSENT in the real container target.

### Known pre-existing failure, not caused by this PR — now empirically confirmed, not just inferred

`tests/test_golden_fixture.py::test_simulating_from_the_committed_fixture_reproduces_the_golden_digest`
and three tests in `tests/test_replay.py` fail in this execution
environment (native Windows Python), calling only pre-existing
`domain.services.simulate`/`worker/__main__.py` code this PR does not
modify. Two independent pieces of evidence, not a hypothesis:

1. **Confirmed present on `main` too.** Checked out `main` in a
   disposable `git worktree` (not a branch switch on the working copy)
   and ran the same four tests, unmodified, in this same Python
   installation — identical failures, byte-for-byte the same digests.
   This PR's changes are not the cause.
2. **Confirmed absent in the real deployment target.** The mandatory
   Docker container acceptance suite (`scripts/run_container_acceptance.sh`,
   run for real, see "Corrections" item 6) reproduces the exact
   historical golden digest inside the actual Linux container this
   worker ships as. The drift is this Windows Python installation's own
   BLAS build (`docker/worker.Dockerfile`'s own Phase 2 acceptance
   criteria already named this exact risk: "Base-image BLAS and SIMD
   differences... where the README's documented fit drift becomes
   visible") — not a defect in the code, and not present in production.

`tests/scenario_platform/test_worker_job_id_mode.py`'s own digest test
was written to compare against a same-process local computation
specifically to stay independent of this drift, rather than inheriting
the historical hard-coded digest — that test passes on this Windows
environment precisely because it never depends on cross-environment
reproducibility in the first place.

## What only a real AWS deployment can verify (cannot be checked here)

1. `terraform apply` for `envs/dev` + `demo_killswitch` actually
   succeeding — IAM `PassRole`/`RunTask` condition debugging is Section
   24's own named risk for this phase, and `terraform validate` does not
   catch a runtime `AccessDenied`.
2. A real Fargate **task** (not just the image, which is now confirmed
   locally) producing a byte-identical array to the local golden fixture
   (Phase 3b acceptance criterion 2) on real AWS network/hardware — the
   Docker acceptance run (Corrections item 6) already confirms the image
   itself reproduces the digest on Linux; what remains AWS-only is
   whether Fargate's specific CPU/hypervisor introduces its own drift,
   which is a narrower, less likely gap than the one just closed.
3. The manifest recording a real Fargate task ARN, and `TaskStartSeconds`/
   `ComputeSeconds` being visible as CloudWatch metrics (acceptance
   criteria 3 and 5).
4. ~~Control-plane Lambda deployed package size staying under 15 MB~~ —
   **resolved locally**: built for real and verified in the actual Lambda
   base image (Corrections item 1); 2.6 MB, well under budget.
5. The demo-cleanup Lambda's `ec2:DeleteVpcEndpoints`/`ecs:StopTask`/
   `ecs:DeregisterTaskDefinition` calls succeeding against real
   endpoint/task/task-definition state transitions — moto's EC2/ECS mocks
   (exercised in `tests/infra/test_demo_killswitch_cleanup.py`) do not
   model deletion/deregistration timing with full fidelity, and moto
   never fails with a transient AWS-side error the way real S3/EC2/ECS
   occasionally do.
6. `scripts/smoke.py` end to end (real SigV4-signed requests against a
   real API Gateway `AWS_IAM` authorizer) — the unauthenticated-request
   rejection is primarily an API-Gateway-configuration property, and the
   Lambda-level check in `control/errors.py::principal_arn` is
   defense-in-depth, not the primary proof.
7. Real measured cost against `docs/aws-demo-runbook.md`'s estimate
   (Cost Explorer is disabled on this account as of the prior turn).

`docs/aws-demo-runbook.md` sequences items 1-2 and 6-7 into a single
bounded demo window with an independent auto-cleanup; item 3 and the
residual sliver of item 2 are observable from that same window's
`terraform output`/CloudWatch/ECR console without extra steps; item 5 is
exercised for real only if the auto-cleanup actually fires during that
window (or is deliberately triggered early to test it).

## Branch and review

Prepared on `feat/aws-phase3b`, opened as a PR against `main`, **not
merged automatically** — per this turn's explicit instruction, merging
is a separate, later decision.
