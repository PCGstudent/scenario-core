# Worker CLI reference (Phase 2)

Concise reference for `scenario_platform.worker` — the `simulate` CLI built by
`docker/worker.Dockerfile`. The authoritative detail lives in the module
docstrings (`src/scenario_platform/worker/__main__.py`,
`src/scenario_platform/worker/errors.py`); this page indexes it and is kept
short deliberately.

## Invocation

```
scenario-worker simulate --artifact-dir DIR --request REQUEST.json --output-dir DIR
```

`--artifact-dir` is a `load_artifact`-format directory (`artifact.json` +
`state.npz`). `--request` is a JSON object; every field maps directly onto
`ScenarioRequest`/`RiskConfig` (`domain/requests.py`) with no coercion —
a fractional, string, or boolean value where an integer is required is
rejected, not reinterpreted. Fields:

| Field | Type | Constraint |
|---|---|---|
| `artifact_id` | string | required, non-empty |
| `model_version` | string | required, non-empty |
| `horizon` | integer | required, >= 1 |
| `n_paths` | integer | required, >= 1 |
| `seed` | integer | required, >= 0 |
| `initial_state` | `"historical_mix"` \| `"latest"` \| `[residual, variance]` | optional, default `"historical_mix"`; array form requires finite values and `variance > 0` |
| `rng_scheme` | string | optional, default `"single"` |
| `return_variance` | boolean | optional, default `false`; when `true`, `variances.npy` is written |
| `risk_levels` | array of numbers | optional, default `[0.95, 0.99]`; each strictly in `(0, 1)`, each checked against the Phase-1 governance policy (`domain/policies.check_metric_request`) |
| `governance` | `{cap, approver}` | optional, passed through to the policy check |

Any field outside this set is rejected (`WorkerInputError`), not ignored — a
typo like `"horizonn"` fails loudly instead of silently falling back to a
default. See `_KNOWN_REQUEST_FIELDS` and `_parse_request` in `__main__.py`
for the exact validation.

`--output-dir` must not already exist with content in it (empty or absent
is fine) — this is a coarse precondition; the actual single-writer guarantee
is the lock file described below, not this check alone.

## Output / completion-manifest contract

On success, `--output-dir` contains:

- `returns.npy` — the simulated paths, `float64`, shape `(n_paths, horizon)`
- `variances.npy` — only when `return_variance: true` was requested
- `risk_report.json` — VaR/ES per requested level, max-drawdown median
- `manifest.json` — written **last**, always

**A consumer must never trust `returns.npy`/`risk_report.json` present
without `manifest.json` also present.** `manifest.json`'s presence is this
worker's own definition of "the result is complete." Every file is staged
in memory first and published with atomic per-file `os.replace` renames, in
a fixed order ending with the manifest — see `_write_outputs_atomically`'s
docstring in `__main__.py` for the full mechanism, including why a
whole-directory rename doesn't work for a bind-mounted `--output-dir`.

**What interruption can leave behind.** This worker cleans up everything it
can on a *handled* failure (a caught exception unwinds and removes anything
this invocation already published). It cannot clean up after being killed
outright — no Python code runs after a SIGKILL. A hard-killed run can leave
a staging subdirectory, a lock file, or (in the narrow window between two
`os.replace` calls) a published data file with no manifest. None of this
is ever mistaken for a complete result by a consumer that correctly gates
on `manifest.json`.

**Single-writer assumption.** Exactly one invocation may publish into a
given `--output-dir` at a time. This is enforced, not merely documented: an
exclusive lock file (`os.O_CREAT | os.O_EXCL`, atomic on POSIX) is claimed
before any staging happens; a second concurrent invocation targeting the
same directory fails closed with `WorkerConcurrentPublishError` (exit
`INPUT`) rather than interleaving its writes with the first.

## Digest definitions

Two distinct SHA-256 digests are recorded in `manifest.json`, and they are
**deliberately different values** — never confuse them:

- **`returns_array_sha256`** — SHA-256 of the contiguous `float64` array
  bytes alone (`np.ascontiguousarray(returns).tobytes()`). This is what the
  Phase-1 golden digest is defined over
  (`sha256:637920e584b8e82449a67b84bfc39b73528256aa6518d8ca8280087b9fb3c255`
  for the fixed golden request against `tests/fixtures/gjr_skewt_v1/`), and
  what every replay test and the acceptance script compare against it.
- **`returns_npy_file_sha256`** — SHA-256 of the complete `.npy` file as
  written to disk, i.e. the array bytes above *plus* NumPy's `.npy` header
  (magic bytes, version, a dtype/shape/order descriptor). This is what a
  consumer that fetches `returns.npy` from storage and hashes it as-is would
  actually get. Recorded for that reason, but never compared against the
  golden value — the golden value was established (Phase 1) over the array
  bytes, not the file bytes, and that comparison is preserved exactly.

`variances_array_sha256` follows the same array-bytes convention when
`variances.npy` is present.

## Exit codes / `error_class`

`src/scenario_platform/worker/errors.py` is the single source of truth.
Summary:

| Exit code | `error_class` | Meaning |
|---|---|---|
| 0 | — | success |
| 2 | `INPUT` | request validation failed, an unsupported operation was requested, a risk level was policy-rejected, or a concurrent publish to the same `--output-dir` was detected |
| 3 | `ARTIFACT_INTEGRITY` | artifact missing/unreadable/malformed/self-inconsistent, or it doesn't match the request's `artifact_id`/`model_version` |
| 4 | `INTERNAL` | anything else unexpected, including the runtime thread-contract check failing |

`137` (`RESOURCE`, i.e. SIGKILL/OOM) and the other infrastructure-classified
values (`TIMEOUT`, `TRANSIENT_INFRA`, `CONFIG`, `UNCLASSIFIED`, `ORPHANED`)
are documented in `errors.py` for completeness but **cannot be emitted or
observed by this process** — Phase 2 has no orchestrator to supply the
corroborating evidence (e.g. `stoppedReason` containing `OutOfMemoryError`)
that a real classifier would require before assigning `RESOURCE`. **Exit
code 137 alone is not proof of an OOM kill** — SIGKILL is also what an
operator's `docker kill`, an orchestrator eviction, or a health-check
timeout sends. A future orchestrator must inspect the actual termination
evidence, not the bare exit code.

## Runtime thread contract

The Dockerfile pins `OMP_NUM_THREADS`/`OPENBLAS_NUM_THREADS`/
`MKL_NUM_THREADS=1`, but env vars alone don't prove the loaded numerical
runtime honoured them — a stale value, a BLAS build that ignores the
variable, or a `docker run -e` override at container-start time would
otherwise go undetected. Before doing any computation, `simulate` calls
`threadpoolctl.threadpool_info()` to inspect the *actual* BLAS/OpenMP thread
pools NumPy/SciPy loaded, and fails closed (`WorkerThreadContractError`,
exit `INTERNAL`) if any pool reports more than one thread. Both the env vars
(as logged, for provenance) and the actual thread-pool inspection are
recorded in `manifest.json`'s `runtime` object (`thread_env`,
`thread_pools`).

## Mandatory container acceptance

`scripts/run_container_acceptance.sh` is the required, reproducible gate —
distinct from the ordinary host test suite, which skips container tests
cleanly when Docker isn't available. The acceptance script does not skip:
it requires a working Docker daemon, builds the current source for
`linux/amd64`, resolves the exact immutable image ID `docker build` just
produced (not a mutable tag), and fails if any of the following is untrue:

1. the build succeeds and reports `linux/amd64`;
2. the image excludes Streamlit/Plotly/PyArrow and is measured (via
   `docker images`' Size column, not `docker inspect --format {{.Size}}`,
   which under-reports on this Docker Desktop version — confirmed against
   an unmodified upstream base image);
3. the container runs as a non-root UID;
4. `threadpoolctl` reports every thread pool at exactly 1 thread;
5. cold-import time is recorded;
6. a single golden replay against that exact image ID reproduces
   `returns_array_sha256` == the Phase-1 golden digest, byte for byte;
7. the full container-gated pytest lane (`tests/test_replay.py -k
   "container or image_"`) passes against that same exact image ID with
   **zero** skips.

Run it with:

```
bash scripts/run_container_acceptance.sh
```

It requires an activated Python environment with the project's dev/worker
dependencies installed (same convention as every other check in this repo)
and a reachable Docker daemon. On a WSL2 development machine where the WSL
distro is not registered in Docker Desktop's own WSL Integration settings,
the script installs a small wrapper at `~/bin/docker` that forwards to the
Windows Docker Desktop client and translates `-v`/`--volume` bind-mount
source paths to Windows form via `wslpath -w` — necessary because the raw
Windows client does not resolve a WSL-style source path and would otherwise
silently mount an empty directory instead of the intended file or directory.
This is a workaround for that one development machine's environment, not a
statement about portability: **repeated executions on the same tested host
prove reproducibility on that host, not portability across a different CPU
host** (a different microarchitecture can select different BLAS SIMD
kernels, which is exactly the Tier-1-bit-identity question Phase 2 exists to
answer — see `docs/architecture/IMPLEMENTATION_PLAN.md`, Phase 2). A run
that has not actually been executed on a second, independent host is not
evidence of that portability, and must not be reported as if it were.
