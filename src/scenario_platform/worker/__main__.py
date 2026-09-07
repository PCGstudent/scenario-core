"""The production worker CLI (architecture plan Section 22: ``worker/__main__.py``,
"CLI: calibrate | simulate | validate | risk").

Phase 2 implements only ``simulate`` -- the request-flow step (Section 6.1,
step 4) that loads a frozen :class:`~scenario_platform.domain.artifacts.ModelArtifact`,
verifies it, calls the existing Phase-1 ``services.simulate``/``services.risk``
functions unchanged, and atomically publishes ``returns.npy``,
``risk_report.json`` and ``manifest.json``. ``calibrate`` and ``validate`` are
Phase 3b's addition (their own Files bullet names them explicitly); this
module is structured as a subcommand dispatcher precisely so adding them
later is a new subparser, not a redesign.

**Local substitution for the AWS-shaped contract, not a competing one.**
Section 6.1 step 4a reads the canonical resolved request from DynamoDB and
the artifact from S3; Phase 2 has neither, so this CLI takes the same two
inputs as plain local paths (``--request`` a JSON file, ``--artifact-dir`` a
:func:`~scenario_platform.domain.serialization.load_artifact` directory).
Every field in the request document maps directly onto the existing
``ScenarioRequest``/``RiskConfig`` domain types (``requests.py``) -- nothing
here is a new domain model, only a thin JSON transport over one.

**Fail-closed, in order:**

1. malformed/incomplete request JSON -> ``WorkerInputError`` (INPUT)
2. artifact missing, unreadable, structurally invalid, or self-inconsistent
   -> ``ArtifactIntegrityError``/``ArtifactValidationError`` (ARTIFACT_INTEGRITY)
3. the loaded artifact is not the one the request named
   -> ``WorkerArtifactMismatchError`` (ARTIFACT_INTEGRITY, Section 8.3)
4. the request's ``model_version`` does not match the artifact's
   -> ``RequestArtifactMismatchError`` (ARTIFACT_INTEGRITY, raised inside
   ``services.simulate`` itself)
5. an unsupported ``rng_scheme`` -> ``NotImplementedError`` (INPUT)
6. anything else unexpected -> INTERNAL

No result file is ever written until every one of the steps above has
already succeeded (see ``_write_outputs_atomically``): all computation runs
to completion in memory first, and only a fully-formed result is ever
staged to disk and atomically published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from scenario_platform.adapters.logging import configure_worker_logging
from scenario_platform.adapters.metrics import emit_duration_ms
from scenario_platform.domain import serialization
from scenario_platform.domain.artifacts import (
    ArtifactIntegrityError,
    ArtifactValidationError,
    ModelArtifact,
)
from scenario_platform.domain.reports import RiskReport
from scenario_platform.domain.requests import RiskConfig, ScenarioRequest
from scenario_platform.domain.services import RequestArtifactMismatchError
from scenario_platform.domain.services import risk as compute_risk
from scenario_platform.domain.services import simulate as run_simulate

from .errors import ExitCode, WorkerArtifactMismatchError, WorkerInputError

LOGGER = configure_worker_logging()

#: Verified and logged at the start of every invocation (Phase-2 acceptance
#: criterion 3: "Threads are pinned and verified at runtime"). This module
#: does not *set* these -- the Dockerfile/entrypoint does, before Python
#: (and therefore numpy/BLAS) ever starts -- it only reads and reports them,
#: because a value logged after the fact is worthless if nothing upstream
#: actually enforced it.
THREAD_ENV_VARS: tuple[str, ...] = (
    "OMP_NUM_THREADS",
    "OPENBLAS_NUM_THREADS",
    "MKL_NUM_THREADS",
)


def _effective_thread_counts() -> dict[str, str | None]:
    return {name: os.environ.get(name) for name in THREAD_ENV_VARS}


def _parse_request(path: Path) -> tuple[str, ScenarioRequest, RiskConfig]:
    """Read and validate the worker's request document.

    Returns ``(expected_artifact_id, scenario_request, risk_config)``.
    Every failure here is a :class:`WorkerInputError` -- this function never
    lets a bare ``KeyError``/``TypeError``/``json.JSONDecodeError`` escape,
    because those would be misclassified as INTERNAL by the top-level
    handler rather than the INPUT failure they actually are.
    """
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

    required = ("artifact_id", "model_version", "horizon", "n_paths", "seed")
    missing = [field for field in required if field not in data]
    if missing:
        raise WorkerInputError(f"request is missing required field(s): {missing}")

    artifact_id = data["artifact_id"]
    if not isinstance(artifact_id, str) or not artifact_id:
        raise WorkerInputError("request.artifact_id must be a non-empty string")

    initial_state: object = data.get("initial_state", "historical_mix")
    if isinstance(initial_state, list):
        if len(initial_state) != 2:
            raise WorkerInputError(
                "request.initial_state as an array must have exactly 2 elements "
                "(residual, variance)"
            )
        try:
            initial_state = (float(initial_state[0]), float(initial_state[1]))
        except (TypeError, ValueError) as exc:
            raise WorkerInputError(f"invalid request.initial_state: {exc}") from exc

    try:
        scenario_request = ScenarioRequest(
            model_version=data["model_version"],
            horizon=int(data["horizon"]),
            n_paths=int(data["n_paths"]),
            seed=int(data["seed"]),
            initial_state=initial_state,  # type: ignore[arg-type]
            rng_scheme=data.get("rng_scheme", "single"),
            return_variance=bool(data.get("return_variance", False)),
        )
    except (TypeError, ValueError) as exc:
        raise WorkerInputError(f"invalid request: {exc}") from exc

    risk_levels = data.get("risk_levels", [0.95, 0.99])
    if not isinstance(risk_levels, list) or not risk_levels:
        raise WorkerInputError("request.risk_levels must be a non-empty array")
    try:
        risk_config = RiskConfig(levels=tuple(float(x) for x in risk_levels))
    except (TypeError, ValueError) as exc:
        raise WorkerInputError(f"invalid request.risk_levels: {exc}") from exc

    return artifact_id, scenario_request, risk_config


def _load_and_verify_artifact(
    artifact_dir: Path, expected_artifact_id: str
) -> ModelArtifact:
    """Load the artifact and enforce Section 8.3's two integrity checks.

    ``serialization.load_artifact`` already calls ``artifact.verify_identity()``
    unconditionally (fail-closed on the artifact disagreeing with *itself*);
    this function adds the second check Section 8.3 names explicitly --
    "compares [the recomputed id] with the id named in the job request" --
    which is a caller/loader-scoped check, not an artifact-scoped one.
    """
    try:
        artifact = serialization.load_artifact(artifact_dir)
    except OSError as exc:
        raise ArtifactIntegrityError(
            f"artifact could not be read from {artifact_dir}: {exc}"
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
    returns: npt.NDArray[np.float64],
    risk_report: RiskReport,
    manifest: dict[str, Any],
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

    ``os.replace`` on a POSIX filesystem is an atomic ``rename(2)``; renaming
    a file within the same directory is always same-filesystem and therefore
    always atomic. The three files are published in a fixed order with
    ``manifest.json`` **last**, deliberately: it is this worker's own
    definition of "the result is complete," so a consumer (or a test) that
    checks for its presence before trusting ``returns.npy``/``risk_report.json``
    can never observe a half-published result. Any failure while staging or
    publishing removes the staging directory and re-raises.
    """
    staging = Path(tempfile.mkdtemp(prefix=".worker-staging-", dir=output_dir))
    try:
        np.save(staging / "returns.npy", returns, allow_pickle=False)
        (staging / "risk_report.json").write_text(
            json.dumps(_risk_report_to_dict(risk_report), indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        (staging / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        os.replace(staging / "returns.npy", output_dir / "returns.npy")
        os.replace(staging / "risk_report.json", output_dir / "risk_report.json")
        os.replace(staging / "manifest.json", output_dir / "manifest.json")
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _cmd_simulate(args: argparse.Namespace) -> int:
    started_at = datetime.now(tz=UTC)
    t0 = time.monotonic()
    artifact_dir = Path(args.artifact_dir)
    request_path = Path(args.request)
    output_dir = Path(args.output_dir)

    LOGGER.info(
        "simulate starting",
        extra={
            "artifact_dir": str(artifact_dir),
            "request_path": str(request_path),
            "output_dir": str(output_dir),
            "thread_env": _effective_thread_counts(),
        },
    )

    error_class: str | None = None
    try:
        if output_dir.exists() and any(output_dir.iterdir()):
            raise WorkerInputError(
                f"output directory {output_dir} already exists and is not empty"
            )

        expected_artifact_id, scenario_request, risk_config = _parse_request(request_path)
        artifact = _load_and_verify_artifact(artifact_dir, expected_artifact_id)

        scenario_set = run_simulate(artifact, scenario_request)
        risk_report = compute_risk(scenario_set, risk_config)

        returns_digest = (
            "sha256:"
            + hashlib.sha256(
                np.ascontiguousarray(scenario_set.returns).tobytes()
            ).hexdigest()
        )

        finished_at = datetime.now(tz=UTC)
        duration_ms = (time.monotonic() - t0) * 1000.0

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
            "outputs": {
                "returns": "returns.npy",
                "risk_report": "risk_report.json",
                "manifest": "manifest.json",
            },
            "returns_shape": list(scenario_set.returns.shape),
            "returns_sha256": returns_digest,
            "started_at": started_at.isoformat(),
            "finished_at": finished_at.isoformat(),
            "duration_ms": duration_ms,
            "runtime": {
                "python_version": platform.python_version(),
                "platform_machine": platform.machine(),
                "platform_system": platform.system(),
                "thread_env": _effective_thread_counts(),
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
        _write_outputs_atomically(output_dir, scenario_set.returns, risk_report, manifest)

        LOGGER.info(
            "simulate succeeded",
            extra={
                "artifact_id": artifact.artifact_id,
                "model_version": artifact.model_version,
                "returns_sha256": returns_digest,
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
        required=True,
        help="Directory holding artifact.json + state.npz (load_artifact's format).",
    )
    simulate_parser.add_argument(
        "--request", required=True, help="Path to the JSON request document."
    )
    simulate_parser.add_argument(
        "--output-dir",
        required=True,
        help="Directory to atomically publish returns.npy/risk_report.json/manifest.json.",
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
