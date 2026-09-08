"""The production worker CLI (architecture plan Section 22: ``worker/__main__.py``,
"CLI: calibrate | simulate | validate | risk").

Phase 2 implements only ``simulate`` -- the request-flow step (Section 6.1,
step 4) that loads a frozen :class:`~scenario_platform.domain.artifacts.ModelArtifact`,
verifies it, calls the existing Phase-1 ``services.simulate``/``services.risk``
functions unchanged, and atomically publishes ``returns.npy``,
``risk_report.json`` and ``manifest.json`` (plus ``variances.npy`` when the
request asks for it). ``calibrate`` and ``validate`` are Phase 3b's addition
(their own Files bullet names them explicitly); this module is structured as
a subcommand dispatcher precisely so adding them later is a new subparser,
not a redesign.

**Local substitution for the AWS-shaped contract, not a competing one.**
Section 6.1 step 4a reads the canonical resolved request from DynamoDB and
the artifact from S3; Phase 2 has neither, so this CLI takes the same two
inputs as plain local paths (``--request`` a JSON file, ``--artifact-dir`` a
:func:`~scenario_platform.domain.serialization.load_artifact` directory).
Every field in the request document maps directly onto the existing
``ScenarioRequest``/``RiskConfig`` domain types (``requests.py``) -- nothing
here is a new domain model, only a thin, strict JSON transport over one. The
transport never coerces (no bare ``int()``/``float()``/``bool()`` on
caller-supplied values): a fractional, string, or boolean value where an
integer is required is rejected, not silently reinterpreted.

**Fail-closed, in order:**

1. the actual, loaded numerical thread pools exceed the single-thread
   contract -> ``WorkerThreadContractError`` (INTERNAL)
2. malformed/incomplete/unknown-field request JSON, an out-of-range or
   wrong-typed field, or a risk level policy rejects -> ``WorkerInputError``
   (INPUT)
3. artifact missing, unreadable, structurally invalid, or self-inconsistent
   -> ``ArtifactIntegrityError``/``ArtifactValidationError`` (ARTIFACT_INTEGRITY)
4. the loaded artifact is not the one the request named
   -> ``WorkerArtifactMismatchError`` (ARTIFACT_INTEGRITY, Section 8.3)
5. the request's ``model_version`` does not match the artifact's
   -> ``RequestArtifactMismatchError`` (ARTIFACT_INTEGRITY, raised inside
   ``services.simulate`` itself)
6. an unsupported ``rng_scheme`` -> ``NotImplementedError`` (INPUT)
7. another invocation is already publishing to, or has already published
   into, the same ``--output-dir`` (checked again *after* this invocation
   holds the publish lock, closing the race where two invocations both
   pass the cheap pre-lock check before either has written anything)
   -> ``WorkerConcurrentPublishError`` (INPUT)
8. anything else unexpected -> INTERNAL

No result file is ever written until every one of the steps above has
already succeeded (see ``_write_outputs_atomically``): all computation runs
to completion in memory first, and only a fully-formed result is ever
staged to disk and atomically published, with ``manifest.json`` written
last as this worker's own definition of "the result is complete."
**A consumer must never trust ``returns.npy``/``risk_report.json`` present
without ``manifest.json`` also present** -- a killed or interrupted process
can still leave partially-staged files behind (the staging directory itself,
or -- in the narrow window between two ``os.replace`` calls -- a published
data file with no manifest yet); this worker cleans up everything it can on
a *handled* exception, but cannot clean up after its own hard kill.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import math
import os
import platform
import shutil
import sys
import tempfile
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import threadpoolctl

from scenario_platform.adapters import job_store, s3_store
from scenario_platform.adapters.logging import configure_worker_logging
from scenario_platform.adapters.metrics import emit_duration_ms
from scenario_platform.domain import serialization
from scenario_platform.domain.artifacts import (
    ArtifactIntegrityError,
    ArtifactValidationError,
    ModelArtifact,
)
from scenario_platform.domain.policies import (
    GovernanceCap,
    PolicyRejection,
    check_metric_request,
)
from scenario_platform.domain.reports import RiskReport
from scenario_platform.domain.requests import RiskConfig, ScenarioRequest
from scenario_platform.domain.serialization import TransportSchemaError
from scenario_platform.domain.services import RequestArtifactMismatchError
from scenario_platform.domain.services import risk as compute_risk
from scenario_platform.domain.services import simulate as run_simulate

from .errors import (
    ExitCode,
    WorkerArtifactMismatchError,
    WorkerConcurrentPublishError,
    WorkerInputError,
    WorkerThreadContractError,
)

LOGGER = configure_worker_logging()

#: Logged for provenance (Phase-2 acceptance criterion 3's "logs the
#: effective thread counts"), but -- unlike Phase 2's original cut -- no
#: longer trusted as the *verification*: an env var can be stale, ignored by
#: the BLAS build, or overridden at `docker run` time without the library
#: that reads it ever re-reading it. See `_verify_thread_contract` below for
#: the actual check.
THREAD_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
)

#: Every field this worker's request transport recognises. Anything else is
#: rejected rather than silently ignored -- a typo'd field name (`"horizonn"`)
#: must fail loudly, not be dropped on the floor while the request quietly
#: falls back to defaults.
_KNOWN_REQUEST_FIELDS = frozenset(
    {
        "artifact_id",
        "model_version",
        "horizon",
        "n_paths",
        "seed",
        "initial_state",
        "rng_scheme",
        "return_variance",
        "risk_levels",
        "governance",
    }
)

_KNOWN_INITIAL_STATE_LITERALS = ("historical_mix", "latest")


def _effective_thread_env() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in THREAD_ENV_VARS}


def _verify_thread_contract() -> list[dict[str, object]]:
    """Inspect the *actually loaded* BLAS/OpenMP thread pools and fail closed
    if any resolved to more than one thread.

    ``threadpoolctl.threadpool_info()`` introspects the real libraries NumPy/
    SciPy loaded into this process -- this is what makes the single-thread
    contract *verified* rather than merely *logged*. Confirmed directly: with
    ``OMP_NUM_THREADS=1``/``OPENBLAS_NUM_THREADS=1``/``MKL_NUM_THREADS=1`` set
    this reports 1 thread; with them overridden to 4 (simulating a
    ``docker run -e`` override of the Dockerfile's baked-in values) it
    correctly reports 4.
    """
    pools: list[dict[str, object]] = list(threadpoolctl.threadpool_info())

    def _num_threads(pool: dict[str, object]) -> int:
        value = pool.get("num_threads")
        return int(value) if isinstance(value, int) else 1

    violations = [pool for pool in pools if _num_threads(pool) > 1]
    if violations:
        raise WorkerThreadContractError(
            "the single-thread contract is violated by the actually loaded "
            f"numerical runtime: {violations}"
        )
    return pools


def _require_field(data: dict[str, object], key: str) -> object:
    if key not in data:
        raise WorkerInputError(f"request is missing required field: {key!r}")
    return data[key]


def _require_strict_int(data: dict[str, object], key: str, *, minimum: int) -> int:
    """A JSON value that is a genuine integer (never a bool, never a float
    that merely happens to be integral, never a numeric string) and at
    least ``minimum``. No ``int()`` coercion anywhere: ``horizon: 5.5`` or
    ``horizon: "5"`` must be rejected, not silently truncated or parsed.
    """
    value = _require_field(data, key)
    if isinstance(value, bool) or not isinstance(value, int):
        raise WorkerInputError(f"request.{key} must be an integer, got {value!r}")
    if value < minimum:
        raise WorkerInputError(f"request.{key} must be >= {minimum}, got {value}")
    return value


def _require_strict_bool(data: dict[str, object], key: str, *, default: bool) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise WorkerInputError(f"request.{key} must be a boolean, got {value!r}")
    return value


def _require_finite_number(value: object, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise WorkerInputError(f"{label} must be a number, got {value!r}")
    number = float(value)
    if not math.isfinite(number):
        raise WorkerInputError(f"{label} must be finite, got {number}")
    return number


def _parse_initial_state(data: dict[str, object]) -> str | tuple[float, float]:
    raw = data.get("initial_state", "historical_mix")
    if isinstance(raw, str):
        if raw not in _KNOWN_INITIAL_STATE_LITERALS:
            raise WorkerInputError(
                f"request.initial_state {raw!r} is not supported; expected one "
                f"of {_KNOWN_INITIAL_STATE_LITERALS} or a [residual, variance] array"
            )
        return raw
    if isinstance(raw, list):
        if len(raw) != 2:
            raise WorkerInputError(
                "request.initial_state as an array must have exactly 2 elements "
                "(residual, variance)"
            )
        residual = _require_finite_number(raw[0], "request.initial_state[0] (residual)")
        variance = _require_finite_number(raw[1], "request.initial_state[1] (variance)")
        if not variance > 0.0:
            raise WorkerInputError(
                f"request.initial_state variance must be strictly positive, got {variance}"
            )
        return (residual, variance)
    raise WorkerInputError(
        f"request.initial_state must be a string or a 2-element array, got {raw!r}"
    )


def _parse_governance(data: dict[str, object]) -> GovernanceCap | None:
    raw = data.get("governance")
    if raw is None:
        return None
    if not isinstance(raw, dict) or "cap" not in raw or "approver" not in raw:
        raise WorkerInputError(
            "request.governance must be an object with 'cap' and 'approver' string fields"
        )
    cap, approver = raw["cap"], raw["approver"]
    if not isinstance(cap, str) or not cap or not isinstance(approver, str) or not approver:
        raise WorkerInputError(
            "request.governance.cap and .approver must be non-empty strings"
        )
    return GovernanceCap(cap=cap, approver=approver)


def _parse_risk_levels(
    data: dict[str, object], governance: GovernanceCap | None
) -> tuple[float, ...]:
    raw = data.get("risk_levels", [0.95, 0.99])
    if not isinstance(raw, list) or not raw:
        raise WorkerInputError("request.risk_levels must be a non-empty array")
    levels: list[float] = []
    for entry in raw:
        level = _require_finite_number(entry, "request.risk_levels entry")
        if not (0.0 < level < 1.0):
            raise WorkerInputError(
                f"request.risk_levels entries must be strictly between 0 and 1, got {level}"
            )
        levels.append(level)
    # Reuse Phase 1's own policy module (never re-derive its rule): a level
    # the tail-metric policy restricts (Section 8.5) must be rejected here,
    # deliberately, as a normal INPUT rejection -- not left to fall through
    # to the generic internal-error handler as an "unexpected" exception.
    for level in levels:
        try:
            check_metric_request("tail expectation", level=level, governance=governance)
        except PolicyRejection as exc:
            raise WorkerInputError(f"risk level {level} rejected by policy: {exc}") from exc
    return tuple(levels)


def _parse_request(path: Path) -> tuple[str, ScenarioRequest, RiskConfig]:
    """Read, parse and strictly validate a request document from a local file
    (Phase 2's ``--request`` path). Delegates the actual field-level
    validation to :func:`_parse_request_data`, shared with Phase 3b's
    ``--job-id`` path (:func:`_run_job_id_mode`), which reads the same field
    shape from a DynamoDB job item instead of a file."""
    try:
        raw_text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise WorkerInputError(f"could not read request file {path}: {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise WorkerInputError(f"request file {path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise WorkerInputError(f"request file {path} must contain a JSON object")
    return _parse_request_data(data)


def _parse_request_data(data: dict[str, object]) -> tuple[str, ScenarioRequest, RiskConfig]:
    """Strictly validate an already-parsed request document.

    Returns ``(expected_artifact_id, scenario_request, risk_config)``.
    Every failure here is a :class:`WorkerInputError` -- this function never
    lets a bare ``KeyError``/``TypeError`` escape, because those would be
    misclassified as INTERNAL by the top-level handler rather than the
    INPUT failure they actually are. Nothing here duplicates a quantitative
    formula: every check is a transport-level shape/type/range check, and
    the actual domain rules (``ScenarioRequest.__post_init__``,
    ``policies.check_metric_request``) still run, unchanged, on the values
    this function produces.
    """
    unknown = set(data) - _KNOWN_REQUEST_FIELDS
    if unknown:
        raise WorkerInputError(f"request contains unsupported field(s): {sorted(unknown)}")

    artifact_id = _require_field(data, "artifact_id")
    if not isinstance(artifact_id, str) or not artifact_id:
        raise WorkerInputError("request.artifact_id must be a non-empty string")

    model_version = _require_field(data, "model_version")
    if not isinstance(model_version, str) or not model_version:
        raise WorkerInputError("request.model_version must be a non-empty string")

    horizon = _require_strict_int(data, "horizon", minimum=1)
    n_paths = _require_strict_int(data, "n_paths", minimum=1)
    seed = _require_strict_int(data, "seed", minimum=0)
    return_variance = _require_strict_bool(data, "return_variance", default=False)
    initial_state = _parse_initial_state(data)

    rng_scheme = data.get("rng_scheme", "single")
    if not isinstance(rng_scheme, str):
        raise WorkerInputError(f"request.rng_scheme must be a string, got {rng_scheme!r}")

    governance = _parse_governance(data)
    risk_levels = _parse_risk_levels(data, governance)

    try:
        scenario_request = ScenarioRequest(
            model_version=model_version,
            horizon=horizon,
            n_paths=n_paths,
            seed=seed,
            initial_state=initial_state,  # type: ignore[arg-type]
            rng_scheme=rng_scheme,  # type: ignore[arg-type]
            return_variance=return_variance,
        )
    except (TypeError, ValueError) as exc:
        raise WorkerInputError(f"invalid request: {exc}") from exc

    risk_config = RiskConfig(levels=risk_levels)
    return artifact_id, scenario_request, risk_config


#: Exceptions load_artifact (or the metadata/state parsing inside it) is
#: known to raise for a malformed-but-present artifact, narrowly scoped to
#: that one call so an unrelated bug elsewhere is never relabelled as an
#: artifact problem. Confirmed empirically against real malformed inputs
#: (bad JSON, bad encoding, a missing metadata key, a missing dataclass
#: field, an unsupported transport schema, a truncated/empty .npz).
_MALFORMED_ARTIFACT_EXCEPTIONS: tuple[type[Exception], ...] = (
    OSError,
    json.JSONDecodeError,
    UnicodeDecodeError,
    KeyError,
    TypeError,
    TransportSchemaError,
    zipfile.BadZipFile,
    EOFError,
)


def _load_and_verify_artifact(
    artifact_dir: Path, expected_artifact_id: str
) -> ModelArtifact:
    """Load the artifact and enforce Section 8.3's two integrity checks.

    ``serialization.load_artifact`` already calls ``artifact.verify_identity()``
    unconditionally (fail-closed on the artifact disagreeing with *itself*,
    raising :class:`ArtifactIntegrityError`, and
    :class:`ArtifactValidationError` for structurally invalid fitted state
    -- both propagate through this function untouched, already correctly
    classified). This function additionally normalises every other
    *malformed-artifact* failure mode load_artifact can raise -- invalid
    JSON, invalid encoding, a missing metadata/state field, an unsupported
    transport schema, a truncated/empty NPZ -- to
    :class:`ArtifactIntegrityError` too, and adds the second check Section
    8.3 names explicitly: "compares [the recomputed id] with the id named
    in the job request."
    """
    try:
        artifact = serialization.load_artifact(artifact_dir)
    except (ArtifactIntegrityError, ArtifactValidationError):
        raise
    except _MALFORMED_ARTIFACT_EXCEPTIONS as exc:
        raise ArtifactIntegrityError(
            f"artifact could not be read from {artifact_dir}: {type(exc).__name__}: {exc}"
        ) from exc

    if artifact.artifact_id != expected_artifact_id:
        raise WorkerArtifactMismatchError(
            f"loaded artifact_id {artifact.artifact_id!r} does not match the "
            f"request's artifact_id {expected_artifact_id!r} -- refusing to "
            "simulate from the wrong artifact"
        )
    return artifact


def _risk_report_to_dict(report: RiskReport) -> dict[str, Any]:
    return {
        "artifact_id": report.artifact_id,
        "horizon": report.horizon,
        "n_paths": report.n_paths,
        "var_es": {
            str(level): {"var": var, "es": es} for level, (var, es) in report.var_es.items()
        },
        "max_drawdown_median": report.max_drawdown_median,
    }


def _write_outputs_atomically(
    output_dir: Path,
    returns_npy_bytes: bytes,
    risk_report: RiskReport,
    manifest: dict[str, Any],
    variances_npy_bytes: bytes | None,
) -> None:
    """Stage every output file, then publish them with atomic per-file renames.

    Staging happens in a hidden subdirectory *inside* ``output_dir`` rather
    than a sibling of it: in the container's real shape, ``--output-dir`` is
    typically a bind-mounted volume itself (Section 6.1 step 4f's local
    substitute for a ``runs/{job_id}/`` prefix) -- a mount point cannot be
    atomically replaced wholesale the way an ordinary directory can, and its
    *parent* is frequently not writable by a non-root runtime user either.
    Staging inside the (already-writable, contract-guaranteed-empty)
    ``output_dir`` sidesteps both problems.

    **Single-writer enforcement.** An exclusive lock file
    (``os.O_CREAT | os.O_EXCL``, atomic on POSIX -- including over NFS on
    modern kernels, though older NFS versions are a documented exception to
    ``O_EXCL`` atomicity) is claimed in ``output_dir`` before any staging
    happens, and released in a ``finally`` regardless of outcome. This
    alone is not sufficient: it stops two invocations from interleaving
    writes *concurrently*, but not a *sequential* race -- writer A and
    writer B can both pass the caller's own "output directory must already
    be empty" precondition before either has written anything, A then
    fully publishes and releases its lock, and B (running later, not at
    the same instant) then acquires the now-free lock with nothing to stop
    it from overwriting A's already-complete result while A's manifest is
    still visible mid-overwrite. Closing that requires a second check taken
    *after* this invocation holds the lock, not just the lock itself --
    see the next paragraph.

    **Recheck under the lock, before staging.** Immediately after
    acquiring the lock (and before the staging directory is even created),
    ``output_dir`` is listed again and anything present other than the
    lock file this invocation just created -- another invocation's
    already-published result, a leftover staging directory from a killed
    invocation, or ordinary unrelated content -- causes an immediate
    :class:`~scenario_platform.worker.errors.WorkerConcurrentPublishError`
    (``INPUT``). Nothing found this way is ever touched, moved, or
    deleted: rejecting is the only action taken, so whatever is already
    there is preserved byte-for-byte. This is what actually closes the
    sequential race above -- by the time B holds the lock, A's published
    files are already visible to this recheck, so B rejects instead of
    overwriting.

    **Publication order and manifest-last.** ``os.replace`` on a POSIX
    filesystem is an atomic ``rename(2)``; renaming a file within the same
    directory is always same-filesystem and therefore always atomic *per
    file*. Files are published in a fixed order with ``manifest.json``
    **last**, deliberately: it is this worker's own definition of "the
    result is complete," so a consumer that checks for its presence before
    trusting the other files can never observe a half-published result as
    complete.

    **On a handled failure**, every file *this invocation* already
    published is removed again (best-effort) before re-raising -- this
    invocation never touches anything it did not itself create, and
    ``manifest.json`` in particular can never be left behind by a failure,
    because it is only ever added to the rollback-tracked list after its
    own ``os.replace`` has already succeeded, at which point there is
    nothing left to fail. Staging-directory creation itself happens inside
    this same protected region (after the lock is held, after the recheck
    above passes): if ``mkdtemp`` itself fails, the lock this invocation
    holds is still released in ``finally`` rather than leaking -- a
    directory-creation failure unrelated to contention must never
    permanently block every future invocation against this
    ``output_dir``. This cannot protect against the process being killed
    outright (no Python code runs to clean up after a SIGKILL) -- that
    residual risk is why consumers must gate on ``manifest.json``, not on
    this function's best effort alone.
    """
    lock_path = output_dir / ".worker-lock"
    try:
        fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        os.close(fd)
    except FileExistsError as exc:
        raise WorkerConcurrentPublishError(
            f"another invocation is already publishing to {output_dir} "
            f"({lock_path.name} already exists)"
        ) from exc

    staging: Path | None = None
    published: list[Path] = []
    try:
        # Recheck under the lock: the caller's own pre-lock emptiness check
        # can pass for two invocations before either has written anything.
        # Only a check performed after this invocation holds the lock
        # exclusively can actually see a prior invocation's completed
        # result (or any other pre-existing content) and refuse to
        # overwrite it -- see the docstring above.
        existing = [entry for entry in output_dir.iterdir() if entry != lock_path]
        if existing:
            raise WorkerConcurrentPublishError(
                f"output directory {output_dir} already contains content "
                f"({sorted(entry.name for entry in existing)!r}) -- refusing "
                "to publish over it"
            )

        staging = Path(tempfile.mkdtemp(prefix=".worker-staging-", dir=output_dir))

        (staging / "returns.npy").write_bytes(returns_npy_bytes)
        (staging / "risk_report.json").write_text(
            json.dumps(_risk_report_to_dict(risk_report), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if variances_npy_bytes is not None:
            (staging / "variances.npy").write_bytes(variances_npy_bytes)
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )

        publish_order = ["returns.npy", "risk_report.json"]
        if variances_npy_bytes is not None:
            publish_order.append("variances.npy")
        publish_order.append("manifest.json")

        for name in publish_order:
            destination = output_dir / name
            os.replace(staging / name, destination)
            published.append(destination)
    except BaseException:
        for path in published:
            path.unlink(missing_ok=True)
        raise
    finally:
        if staging is not None:
            shutil.rmtree(staging, ignore_errors=True)
        lock_path.unlink(missing_ok=True)


def _resolve_simulate_inputs(
    args: argparse.Namespace,
) -> tuple[Path, str, ScenarioRequest, RiskConfig, Path, str | None, Any, Any]:
    """Resolve ``(artifact_dir, expected_artifact_id, scenario_request,
    risk_config, output_dir, job_id_for_s3_upload, artifact_tmp, output_tmp)``
    from either the local-path arguments (Phase 2) or ``--job-id`` (Phase 3b,
    Section 6.1 step 4a). ``artifact_tmp``/``output_tmp`` are the
    ``TemporaryDirectory`` context managers backing the job-id path's S3
    downloads/uploads (``None`` in local-path mode) -- the caller is
    responsible for cleaning them up once done.
    """
    local_args = (args.artifact_dir, args.request, args.output_dir)
    if args.job_id is not None:
        if any(a is not None for a in local_args):
            raise WorkerInputError(
                "--job-id is mutually exclusive with --artifact-dir/--request/--output-dir"
            )
        job = job_store.get_job(args.job_id)
        if job is None:
            raise WorkerInputError(f"no job found for job_id={args.job_id!r}")
        if job.get("status") != "RUNNING":
            raise WorkerInputError(
                f"job {args.job_id!r} is {job.get('status')!r}, not RUNNING"
            )
        request_data = job_store.to_native(job["request"])
        expected_artifact_id, scenario_request, risk_config = _parse_request_data(
            request_data
        )

        artifact_tmp = tempfile.TemporaryDirectory(prefix="job-artifact-")
        output_tmp = tempfile.TemporaryDirectory(prefix="job-output-")
        artifact_dir = Path(artifact_tmp.name)
        output_dir = Path(output_tmp.name)
        try:
            s3_store.download_artifact(expected_artifact_id, artifact_dir)
        except s3_store.ArtifactNotFound as exc:
            # Section 8.3: "artifact missing ... on the loader" is
            # ARTIFACT_INTEGRITY, not an unrelated INTERNAL failure -- the
            # same fail-closed rule _load_and_verify_artifact already
            # applies to a missing/malformed LOCAL artifact directory,
            # extended here to a genuinely missing S3 object (never a
            # permissions/config problem or an exhausted transient retry,
            # both of which s3_store.download_artifact classifies
            # separately and lets propagate as-is below).
            artifact_tmp.cleanup()
            output_tmp.cleanup()
            raise ArtifactIntegrityError(
                f"artifact {expected_artifact_id!r} not found in S3: {exc}"
            ) from exc
        except (
            s3_store.ArtifactAccessConfigError,
            s3_store.ArtifactTransientError,
            s3_store.ArtifactUnknownError,
        ):
            # Deliberately NOT mapped to ArtifactIntegrityError: a
            # permissions/endpoint-policy misconfiguration or an exhausted
            # transient retry says nothing about whether the artifact
            # itself is intact, and Section 8.3's ARTIFACT_INTEGRITY
            # error_class specifically claims "storage mutation or a
            # wrong-object read" -- a claim neither of these failures
            # supports. Falls through to _cmd_simulate's generic
            # exception handler (INTERNAL), which already logs the full
            # cause with exc_info=True.
            artifact_tmp.cleanup()
            output_tmp.cleanup()
            raise
        return (
            artifact_dir,
            expected_artifact_id,
            scenario_request,
            risk_config,
            output_dir,
            args.job_id,
            artifact_tmp,
            output_tmp,
        )

    if any(a is None for a in local_args):
        raise WorkerInputError(
            "either --job-id, or all three of --artifact-dir/--request/--output-dir, "
            "must be supplied"
        )
    artifact_dir = Path(args.artifact_dir)
    expected_artifact_id, scenario_request, risk_config = _parse_request(Path(args.request))
    output_dir = Path(args.output_dir)
    return (
        artifact_dir,
        expected_artifact_id,
        scenario_request,
        risk_config,
        output_dir,
        None,
        None,
        None,
    )


def _cmd_simulate(args: argparse.Namespace) -> int:
    started_at = datetime.now(tz=UTC)
    t0 = time.monotonic()

    error_class: str | None = None
    artifact_tmp: Any = None
    output_tmp: Any = None
    try:
        (
            artifact_dir,
            expected_artifact_id,
            scenario_request,
            risk_config,
            output_dir,
            job_id_for_upload,
            artifact_tmp,
            output_tmp,
        ) = _resolve_simulate_inputs(args)

        LOGGER.info(
            "simulate starting",
            extra={
                "artifact_dir": str(artifact_dir),
                "output_dir": str(output_dir),
                "job_id": job_id_for_upload,
                "thread_env": _effective_thread_env(),
            },
        )

        thread_pools = _verify_thread_contract()

        if output_dir.exists() and any(output_dir.iterdir()):
            raise WorkerInputError(
                f"output directory {output_dir} already exists and is not empty"
            )

        artifact = _load_and_verify_artifact(artifact_dir, expected_artifact_id)

        scenario_set = run_simulate(artifact, scenario_request)
        risk_report = compute_risk(scenario_set, risk_config)

        # Two distinct digests, deliberately never confused (see the
        # manifest field comments below): the ARRAY digest is sha256 of the
        # contiguous float64 array bytes alone (`ndarray.tobytes()`) -- this
        # is what the Phase-1 golden value is defined over, and what every
        # replay test compares. The FILE digest is sha256 of the complete
        # .npy file (a small header -- magic bytes, version, a dtype/shape/
        # order descriptor -- prepended to those same array bytes); recorded
        # separately because it is what a consumer fetching the file from
        # storage and hashing it as-is would actually get, and the two must
        # never be silently treated as interchangeable.
        returns_array_bytes = np.ascontiguousarray(scenario_set.returns).tobytes()
        returns_digest = "sha256:" + hashlib.sha256(returns_array_bytes).hexdigest()
        returns_npy_buffer = io.BytesIO()
        np.save(returns_npy_buffer, scenario_set.returns, allow_pickle=False)
        returns_npy_bytes = returns_npy_buffer.getvalue()
        returns_npy_file_digest = "sha256:" + hashlib.sha256(returns_npy_bytes).hexdigest()

        variances_digest = None
        variances_npy_bytes = None
        if scenario_set.variances is not None:
            variances_array_bytes = np.ascontiguousarray(scenario_set.variances).tobytes()
            variances_digest = "sha256:" + hashlib.sha256(variances_array_bytes).hexdigest()
            variances_npy_buffer = io.BytesIO()
            np.save(variances_npy_buffer, scenario_set.variances, allow_pickle=False)
            variances_npy_bytes = variances_npy_buffer.getvalue()

        finished_at = datetime.now(tz=UTC)
        duration_ms = (time.monotonic() - t0) * 1000.0

        outputs: dict[str, str] = {
            "returns": "returns.npy",
            "risk_report": "risk_report.json",
            "manifest": "manifest.json",
        }
        if scenario_set.variances is not None:
            outputs["variances"] = "variances.npy"

        manifest: dict[str, Any] = {
            "schema_version": "scenario-core/worker-manifest/v1",
            "operation": "simulate",
            "artifact_id": artifact.artifact_id,
            "model_version": artifact.model_version,
            "family": artifact.family,
            "request": {
                "horizon": scenario_request.horizon,
                "n_paths": scenario_request.n_paths,
                "seed": scenario_request.seed,
                "initial_state": (
                    list(scenario_request.initial_state)
                    if isinstance(scenario_request.initial_state, tuple)
                    else scenario_request.initial_state
                ),
                "rng_scheme": scenario_request.rng_scheme,
                "return_variance": scenario_request.return_variance,
            },
            "risk_levels": list(risk_config.levels),
            "outputs": outputs,
            "returns_shape": list(scenario_set.returns.shape),
            # "Scenario array digest" == sha256 of the contiguous float64
            # ARRAY BYTES (`ndarray.tobytes()`), never of the complete .npy
            # file (which additionally carries a header: magic bytes,
            # version, a dtype/shape/order descriptor, and padding). The two
            # are deliberately different digests -- see
            # `returns_npy_file_sha256` -- and only the array-bytes digest
            # is ever compared against the Phase-1 golden value
            # (sha256:637920e5...fb3c255).
            "returns_array_sha256": returns_digest,
            "returns_npy_file_sha256": returns_npy_file_digest,
            "variances_shape": (
                list(scenario_set.variances.shape)
                if scenario_set.variances is not None
                else None
            ),
            "variances_array_sha256": variances_digest,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": duration_ms,
            "runtime": {
                "python_version": platform.python_version(),
                "platform_machine": platform.machine(),
                "platform_system": platform.system(),
                "thread_env": _effective_thread_env(),
                "thread_pools": thread_pools,
                "worker_image_ref": os.environ.get("WORKER_IMAGE_REF"),
                "worker_git_sha": os.environ.get("WORKER_GIT_SHA"),
            },
        }

        # Everything above is pure computation held in memory; only now is
        # anything written to disk. output_dir itself (created empty here if
        # it does not already exist -- e.g. a fresh local path, as opposed to
        # a pre-existing bind-mounted volume) holds no result files until
        # every one of them is atomically published into it.
        output_dir.mkdir(parents=True, exist_ok=True)
        _write_outputs_atomically(
            output_dir, returns_npy_bytes, risk_report, manifest, variances_npy_bytes
        )

        if job_id_for_upload is not None:
            # Section 6.1 step 4f's AWS-shaped destination. Each invocation
            # stages into its own immutable attempt prefix; a conditional
            # stable manifest commits exactly one complete attempt.
            try:
                s3_store.upload_run_outputs(job_id_for_upload, output_dir)
            except s3_store.ConcurrentPublishSuperseded as exc:
                # A retried invocation of the SAME job (Section 6.3: retries
                # are safe because a re-run is deterministic) whose manifest
                # write lost the race to an earlier attempt's -- this
                # invocation's own computation is correct and complete, it
                # simply is not the copy a reader will see. That is success,
                # not failure: raising this onward would report SUCCESS as
                # an error, and the classifier has no error_class for "the
                # answer was right but someone else's identical copy was
                # published first."
                LOGGER.info(
                    "simulate succeeded but manifest publish was superseded "
                    "by a concurrent/earlier invocation of the same job",
                    extra={"job_id": job_id_for_upload, "reason": str(exc)},
                )

        LOGGER.info(
            "simulate succeeded",
            extra={
                "artifact_id": artifact.artifact_id,
                "model_version": artifact.model_version,
                "returns_array_sha256": returns_digest,
                "returns_shape": list(scenario_set.returns.shape),
                "duration_ms": duration_ms,
            },
        )
        return int(ExitCode.SUCCESS)

    except WorkerInputError as exc:
        error_class = "INPUT"
        LOGGER.error(
            "simulate failed", extra={"error_class": error_class, "error": str(exc)}
        )
        return int(ExitCode.INPUT)
    except NotImplementedError as exc:
        error_class = "INPUT"
        LOGGER.error(
            "simulate failed: unsupported operation",
            extra={"error_class": error_class, "error": str(exc)},
        )
        return int(ExitCode.INPUT)
    except (
        ArtifactIntegrityError,
        ArtifactValidationError,
        WorkerArtifactMismatchError,
        RequestArtifactMismatchError,
    ) as exc:
        error_class = "ARTIFACT_INTEGRITY"
        LOGGER.error(
            "simulate failed",
            extra={
                "error_class": error_class,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        return int(ExitCode.ARTIFACT_INTEGRITY)
    except (
        s3_store.ArtifactAccessConfigError,
        s3_store.ArtifactTransientError,
        s3_store.ArtifactUnknownError,
    ) as exc:
        code = (
            ExitCode.CONFIG
            if isinstance(exc, s3_store.ArtifactAccessConfigError)
            else ExitCode.TRANSIENT_INFRA
            if isinstance(exc, s3_store.ArtifactTransientError)
            else ExitCode.UNCLASSIFIED
        )
        error_class = code.name
        LOGGER.error(
            "artifact download failed",
            exc_info=True,
            extra={"error_class": error_class, "error": str(exc)},
        )
        return int(code)
    except Exception as exc:  # noqa: BLE001 -- the deliberate, documented INTERNAL catch-all
        error_class = "INTERNAL"
        LOGGER.error(
            "simulate failed: internal error",
            exc_info=True,
            extra={
                "error_class": error_class,
                "error": str(exc),
                "error_type": type(exc).__name__,
            },
        )
        return int(ExitCode.INTERNAL)
    finally:
        emit_duration_ms(
            operation="simulate",
            duration_ms=(time.monotonic() - t0) * 1000.0,
            error_class=error_class,
        )
        if artifact_tmp is not None:
            artifact_tmp.cleanup()
        if output_tmp is not None:
            output_tmp.cleanup()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="scenario-worker",
        description=(
            "Quantitative worker CLI over the Phase-1 domain services "
            "(architecture plan Section 22)."
        ),
    )
    # argparse's own parse failures (unknown subcommand, missing required
    # flag) exit 2 by its own convention -- which already equals
    # ExitCode.INPUT, so no extra wrapping is needed for that path.
    subparsers = parser.add_subparsers(dest="command", required=True)

    simulate_parser = subparsers.add_parser(
        "simulate",
        help="Simulate scenario paths and a risk report from a frozen ModelArtifact.",
    )
    simulate_parser.add_argument(
        "--artifact-dir",
        help="Directory holding artifact.json + state.npz (load_artifact's format). "
        "Mutually exclusive with --job-id.",
    )
    simulate_parser.add_argument(
        "--request",
        help="Path to the JSON request document. Mutually exclusive with --job-id.",
    )
    simulate_parser.add_argument(
        "--output-dir",
        help="Directory to atomically publish returns.npy/risk_report.json/manifest.json. "
        "Mutually exclusive with --job-id.",
    )
    simulate_parser.add_argument(
        "--job-id",
        help="Phase 3b mode (architecture plan Section 6.1 step 4): read the job's "
        "canonical resolved request from DynamoDB and its artifact from S3 "
        "(scenario_platform.adapters.{job_store,s3_store}), then publish "
        "returns.npy/risk_report.json/manifest.json to S3 under runs/{job_id}/ "
        "instead of a local --output-dir. Mutually exclusive with the three "
        "local-path arguments above -- this is what Section 6.1's ECS "
        "container override actually passes: "
        'command=["simulate", "--job-id", "{job_id}"].',
    )
    simulate_parser.set_defaults(handler=_cmd_simulate)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    handler: Any = args.handler
    return int(handler(args))


if __name__ == "__main__":
    sys.exit(main())
