"""Proves the Phase-2 worker's Tier-1 replay contract and its fail-closed behaviour.

Two groups of tests:

1. **Direct entry-point tests** (no Docker required, always run): call
   ``scenario_platform.worker.__main__.main(argv)`` -- the real CLI entry
   point -- against local paths. These are the fast, always-collected
   majority: golden-digest replay, every documented failure mode, the
   exit-code table, atomic-publish behaviour, and structured-log shape.

2. **Container tests** (gated on Docker and the built image being present,
   skipped -- never failed -- otherwise): the same golden replay run
   through ``docker run`` against the actual built image, plus the
   image-level acceptance criteria (platform, non-root user, absence of
   prohibited packages, size ceiling). These prove the properties that only
   the container itself can prove; the direct entry-point tests above
   already prove the worker's *logic* is correct without needing Docker at
   all, so CI environments without a Docker daemon still exercise the vast
   majority of this file.

Golden values (must stay in sync with ``tests/test_golden_fixture.py``,
which is their source of truth and where they were first recorded):
``tests/fixtures/gjr_skewt_v1/``, artifact_id
``sha256:fdf0b19144e0e899f29eb88aad30af4bb248deb7986ce34879d6cd00be29913f``,
model_version ``gjr-skewt-20260907-1``, request
``(horizon=252, n_paths=1000, seed=42)`` -> scenario digest
``sha256:637920e584b8e82449a67b84bfc39b73528256aa6518d8ca8280087b9fb3c255``.
"""

from __future__ import annotations

import io
import json
import logging
import shutil
import subprocess
from pathlib import Path

import numpy as np
import pytest

from scenario_platform.worker.__main__ import main
from scenario_platform.worker.errors import ERROR_CLASS_BY_EXIT_CODE, ExitCode

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "gjr_skewt_v1"
GOLDEN_ARTIFACT_ID = (
    "sha256:fdf0b19144e0e899f29eb88aad30af4bb248deb7986ce34879d6cd00be29913f"
)
GOLDEN_MODEL_VERSION = "gjr-skewt-20260907-1"
GOLDEN_SCENARIO_DIGEST = (
    "sha256:637920e584b8e82449a67b84bfc39b73528256aa6518d8ca8280087b9fb3c255"
)

IMAGE_TAG = "scenario-core-worker:phase2"
DOCKERFILE = Path(__file__).parent.parent / "docker" / "worker.Dockerfile"
REPO_ROOT = Path(__file__).parent.parent


def _golden_request(**overrides: object) -> dict[str, object]:
    request: dict[str, object] = {
        "artifact_id": GOLDEN_ARTIFACT_ID,
        "model_version": GOLDEN_MODEL_VERSION,
        "horizon": 252,
        "n_paths": 1000,
        "seed": 42,
    }
    request.update(overrides)
    return request


def _write_request(path: Path, request: dict[str, object]) -> None:
    path.write_text(json.dumps(request), encoding="utf-8")


def _copy_fixture(tmp_path: Path, name: str = "artifact") -> Path:
    """A mutable copy of the committed golden fixture -- tests that tamper
    with an artifact must never touch the committed files themselves."""
    destination = tmp_path / name
    shutil.copytree(FIXTURE_DIR, destination)
    return destination


def _run_worker(argv: list[str]) -> tuple[int, list[dict[str, object]]]:
    """Call the real CLI entry point in-process, capturing its JSON log lines.

    Returns ``(exit_code, parsed_json_log_lines)``. Runs in-process (not a
    subprocess) so this is fast and still exercises ``main()`` exactly as
    ``python -m scenario_platform.worker`` would -- the same function the
    Dockerfile's ENTRYPOINT calls.
    """
    buffer = io.StringIO()
    logger = logging.getLogger("scenario_platform.worker")
    original_handlers = list(logger.handlers)
    handler = logging.StreamHandler(buffer)
    from scenario_platform.adapters.logging import JsonFormatter

    handler.setFormatter(JsonFormatter())
    logger.handlers = [handler]
    try:
        exit_code = main(argv)
    finally:
        logger.handlers = original_handlers

    lines = [json.loads(line) for line in buffer.getvalue().splitlines() if line.strip()]
    return exit_code, lines


# ---------------------------------------------------------------------------
# 1. Golden replay through the real worker entry point
# ---------------------------------------------------------------------------


def test_golden_replay_reproduces_the_exact_scenario_digest(tmp_path):
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    output_dir = tmp_path / "output"

    exit_code, _logs = _run_worker(
        [
            "simulate",
            "--artifact-dir",
            str(FIXTURE_DIR),
            "--request",
            str(request_path),
            "--output-dir",
            str(output_dir),
        ]
    )

    assert exit_code == ExitCode.SUCCESS
    returns = np.load(output_dir / "returns.npy")
    digest = (
        "sha256:"
        + __import__("hashlib").sha256(np.ascontiguousarray(returns).tobytes()).hexdigest()
    )
    assert digest == GOLDEN_SCENARIO_DIGEST


def test_golden_replay_is_repeatable_across_independent_invocations(tmp_path):
    """Two independent calls to main() -- fresh process-level state each
    time is not being exercised here (that is the container tests' job),
    but this proves the worker's own logic carries no incidental state
    between runs (a module-level cache, a mutated default, ...)."""
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())

    digests = []
    for i in range(2):
        output_dir = tmp_path / f"output_{i}"
        exit_code, _ = _run_worker(
            [
                "simulate",
                "--artifact-dir",
                str(FIXTURE_DIR),
                "--request",
                str(request_path),
                "--output-dir",
                str(output_dir),
            ]
        )
        assert exit_code == ExitCode.SUCCESS
        returns = np.load(output_dir / "returns.npy")
        digests.append(
            "sha256:"
            + __import__("hashlib")
            .sha256(np.ascontiguousarray(returns).tobytes())
            .hexdigest()
        )
    assert digests[0] == digests[1] == GOLDEN_SCENARIO_DIGEST


# ---------------------------------------------------------------------------
# 2. Failure modes: each maps to the documented exit code, and none writes
#    a final output file.
# ---------------------------------------------------------------------------


def _assert_fails_closed(tmp_path, argv_kwargs, expected_exit_code):
    output_dir = tmp_path / "output"
    exit_code, logs = _run_worker(
        [
            "simulate",
            "--artifact-dir",
            str(argv_kwargs["artifact_dir"]),
            "--request",
            str(argv_kwargs["request_path"]),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == expected_exit_code
    assert not output_dir.exists() or not any(output_dir.iterdir())
    error_logs = [line for line in logs if line["level"] == "ERROR"]
    assert error_logs, "a failure must log at ERROR level"
    return exit_code, logs


def test_malformed_request_json_is_rejected(tmp_path):
    request_path = tmp_path / "request.json"
    request_path.write_text("not json", encoding="utf-8")
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": FIXTURE_DIR, "request_path": request_path},
        ExitCode.INPUT,
    )


def test_request_missing_a_required_field_is_rejected(tmp_path):
    request_path = tmp_path / "request.json"
    incomplete = _golden_request()
    del incomplete["seed"]
    _write_request(request_path, incomplete)
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": FIXTURE_DIR, "request_path": request_path},
        ExitCode.INPUT,
    )


def test_unsupported_rng_scheme_is_rejected():
    """An unsupported operation the request asked for -- not an artifact
    problem -- so this is INPUT, not ARTIFACT_INTEGRITY."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        request_path = tmp_path / "request.json"
        _write_request(request_path, _golden_request(rng_scheme="sharded-v1"))
        _assert_fails_closed(
            tmp_path,
            {"artifact_dir": FIXTURE_DIR, "request_path": request_path},
            ExitCode.INPUT,
        )


def test_missing_artifact_directory_is_rejected(tmp_path):
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": tmp_path / "does-not-exist", "request_path": request_path},
        ExitCode.ARTIFACT_INTEGRITY,
    )


def test_tampered_artifact_parameter_is_rejected(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    metadata_path = artifact_dir / "artifact.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["params"]["mu"] = data["params"]["mu"] + 1e-6
    metadata_path.write_text(json.dumps(data), encoding="utf-8")

    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": artifact_dir, "request_path": request_path},
        ExitCode.ARTIFACT_INTEGRITY,
    )


def test_tampered_structural_diagnostics_is_rejected(tmp_path):
    """The specific Phase-1 review finding: a tampered diagnostics field
    (not a tampered parameter) must be caught here too, all the way through
    the worker CLI, not just at the domain layer."""
    artifact_dir = _copy_fixture(tmp_path)
    metadata_path = artifact_dir / "artifact.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["diagnostics"]["finite_fourth_moment"] = not data["diagnostics"][
        "finite_fourth_moment"
    ]
    metadata_path.write_text(json.dumps(data), encoding="utf-8")

    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": artifact_dir, "request_path": request_path},
        ExitCode.ARTIFACT_INTEGRITY,
    )


def test_corrupted_state_array_is_rejected(tmp_path):
    """A truncated/mismatched state.npz -- corrupted or mismatched state,
    distinct from a tampered artifact.json field."""
    artifact_dir = _copy_fixture(tmp_path)
    with np.load(artifact_dir / "state.npz") as npz:
        residuals = npz["fitted_residuals"]
        variances = npz["fitted_variances"]
    np.savez(
        artifact_dir / "state.npz",
        fitted_residuals=residuals[:-1],  # now mismatched in length
        fitted_variances=variances,
    )

    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": artifact_dir, "request_path": request_path},
        ExitCode.ARTIFACT_INTEGRITY,
    )


def test_unsupported_artifact_schema_version_is_rejected(tmp_path):
    """An unsupported *artifact* schema (as opposed to an unsupported
    *request* operation, covered by the rng_scheme test above) -- caught by
    ModelArtifact.__post_init__'s own schema_version check."""
    artifact_dir = _copy_fixture(tmp_path)
    metadata_path = artifact_dir / "artifact.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["schema_version"] = "scenario-core/artifact/v0"
    metadata_path.write_text(json.dumps(data), encoding="utf-8")

    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": artifact_dir, "request_path": request_path},
        ExitCode.ARTIFACT_INTEGRITY,
    )


def test_request_artifact_id_mismatch_is_rejected(tmp_path):
    """The request names an artifact_id different from the one on disk --
    Section 8.3's own worked example ("recomputes artifact_id... compares
    with the id named in the job request")."""
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request(artifact_id="sha256:" + "0" * 64))
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": FIXTURE_DIR, "request_path": request_path},
        ExitCode.ARTIFACT_INTEGRITY,
    )


def test_request_model_version_mismatch_is_rejected(tmp_path):
    """The request's model_version does not match the artifact's -- caught
    inside services.simulate itself (RequestArtifactMismatchError)."""
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request(model_version="some-other-model"))
    _assert_fails_closed(
        tmp_path,
        {"artifact_dir": FIXTURE_DIR, "request_path": request_path},
        ExitCode.ARTIFACT_INTEGRITY,
    )


def test_nonempty_output_directory_is_rejected(tmp_path):
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "stale.txt").write_text("leftover", encoding="utf-8")

    exit_code, logs = _run_worker(
        [
            "simulate",
            "--artifact-dir",
            str(FIXTURE_DIR),
            "--request",
            str(request_path),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == ExitCode.INPUT
    # The pre-existing stale file must survive untouched -- the worker must
    # not have deleted or overwritten anything it does not own.
    assert (output_dir / "stale.txt").read_text(encoding="utf-8") == "leftover"
    assert not (output_dir / "manifest.json").exists()


# ---------------------------------------------------------------------------
# 3. Exit-code / error_class table and structured logging
# ---------------------------------------------------------------------------


def test_exit_code_table_covers_every_class_this_process_emits():
    assert ERROR_CLASS_BY_EXIT_CODE[ExitCode.SUCCESS] is None
    assert ERROR_CLASS_BY_EXIT_CODE[ExitCode.INPUT] == "INPUT"
    assert ERROR_CLASS_BY_EXIT_CODE[ExitCode.ARTIFACT_INTEGRITY] == "ARTIFACT_INTEGRITY"
    assert ERROR_CLASS_BY_EXIT_CODE[ExitCode.INTERNAL] == "INTERNAL"
    # 1 and the 128+/137 signal range are deliberately never assigned to a
    # class this process chooses on purpose (see errors.py's module
    # docstring) -- 1 is not a key in the emitted-by-this-process set.
    assert ExitCode.SUCCESS == 0
    assert 1 not in (ExitCode.INPUT, ExitCode.ARTIFACT_INTEGRITY, ExitCode.INTERNAL)


def test_successful_run_emits_well_formed_json_logs_with_no_full_array(tmp_path):
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    output_dir = tmp_path / "output"

    exit_code, logs = _run_worker(
        [
            "simulate",
            "--artifact-dir",
            str(FIXTURE_DIR),
            "--request",
            str(request_path),
            "--output-dir",
            str(output_dir),
        ]
    )
    assert exit_code == ExitCode.SUCCESS
    assert logs, "expected at least one structured log line"
    for line in logs:
        assert {"timestamp", "level", "logger", "message"} <= set(line)
        # No full returns array (1000x252 floats) ever appears in a log line.
        serialized = json.dumps(line)
        assert len(serialized) < 5_000, "a log line is suspiciously large for a summary"
        assert "returns_shape" not in line or isinstance(line["returns_shape"], list)


def test_effective_thread_counts_are_logged_at_startup(tmp_path, monkeypatch):
    monkeypatch.setenv("OMP_NUM_THREADS", "1")
    monkeypatch.setenv("OPENBLAS_NUM_THREADS", "1")
    monkeypatch.setenv("MKL_NUM_THREADS", "1")
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())

    _exit_code, logs = _run_worker(
        [
            "simulate",
            "--artifact-dir",
            str(FIXTURE_DIR),
            "--request",
            str(request_path),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )
    starting_logs = [line for line in logs if line["message"] == "simulate starting"]
    assert starting_logs
    thread_env = starting_logs[0]["thread_env"]
    assert thread_env == {
        "OMP_NUM_THREADS": "1",
        "OPENBLAS_NUM_THREADS": "1",
        "MKL_NUM_THREADS": "1",
    }


# ---------------------------------------------------------------------------
# 4. Container tests -- gated on Docker and the built image
# ---------------------------------------------------------------------------


def _docker_available() -> bool:
    return shutil.which("docker") is not None


def _image_available() -> bool:
    if not _docker_available():
        return False
    result = subprocess.run(
        ["docker", "image", "inspect", IMAGE_TAG],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


_requires_built_image = pytest.mark.skipif(
    not _image_available(),
    reason=(
        f"requires Docker and a locally built {IMAGE_TAG} image "
        f"(docker build -f {DOCKERFILE} -t {IMAGE_TAG} .); this test never "
        "builds the image itself, matching the repository's established "
        "pattern of skipping rather than silently doing extra work"
    ),
)


def _run_container(
    *, artifact_dir: Path, request_path: Path, output_dir: Path
) -> subprocess.CompletedProcess[str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    return subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "-v",
            f"{artifact_dir}:/artifact:ro",
            "-v",
            f"{request_path}:/request.json:ro",
            "-v",
            f"{output_dir}:/output",
            IMAGE_TAG,
            "simulate",
            "--artifact-dir",
            "/artifact",
            "--request",
            "/request.json",
            "--output-dir",
            "/output",
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@_requires_built_image
def test_container_golden_replay_reproduces_the_exact_scenario_digest(tmp_path):
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    output_dir = tmp_path / "output"

    result = _run_container(
        artifact_dir=FIXTURE_DIR, request_path=request_path, output_dir=output_dir
    )
    assert result.returncode == 0, result.stdout + result.stderr
    returns = np.load(output_dir / "returns.npy")
    digest = (
        "sha256:"
        + __import__("hashlib").sha256(np.ascontiguousarray(returns).tobytes()).hexdigest()
    )
    assert digest == GOLDEN_SCENARIO_DIGEST


@_requires_built_image
def test_container_golden_replay_is_repeatable(tmp_path):
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())

    digests = []
    for i in range(2):
        output_dir = tmp_path / f"output_{i}"
        result = _run_container(
            artifact_dir=FIXTURE_DIR, request_path=request_path, output_dir=output_dir
        )
        assert result.returncode == 0, result.stdout + result.stderr
        returns = np.load(output_dir / "returns.npy")
        digests.append(
            "sha256:"
            + __import__("hashlib")
            .sha256(np.ascontiguousarray(returns).tobytes())
            .hexdigest()
        )
    assert digests[0] == digests[1] == GOLDEN_SCENARIO_DIGEST


@_requires_built_image
def test_container_artifact_integrity_failure_writes_no_final_output(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    metadata_path = artifact_dir / "artifact.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    data["diagnostics"]["finite_fourth_moment"] = not data["diagnostics"][
        "finite_fourth_moment"
    ]
    metadata_path.write_text(json.dumps(data), encoding="utf-8")

    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    output_dir = tmp_path / "output"

    result = _run_container(
        artifact_dir=artifact_dir, request_path=request_path, output_dir=output_dir
    )
    assert result.returncode == ExitCode.ARTIFACT_INTEGRITY, result.stdout + result.stderr
    assert not any(output_dir.iterdir())


@_requires_built_image
def test_image_platform_is_linux_amd64():
    result = subprocess.run(
        ["docker", "inspect", IMAGE_TAG, "--format", "{{.Os}}/{{.Architecture}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "linux/amd64"


@_requires_built_image
def test_image_runs_as_a_non_root_user():
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "id",
            IMAGE_TAG,
            "-u",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    uid = int(result.stdout.strip())
    assert uid != 0


@_requires_built_image
def test_image_excludes_prohibited_packages():
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "--entrypoint",
            "python",
            IMAGE_TAG,
            "-m",
            "pip",
            "list",
            "--format",
            "freeze",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    installed = result.stdout.lower()
    for prohibited in ("streamlit", "plotly", "pyarrow"):
        assert prohibited not in installed, f"{prohibited} must not be in the worker image"


#: Longest suffix first -- "670MB" ends with "B" too, so checking "B" before
#: "MB"/"kB"/"GB" would wrongly match the 1-byte unit on every multi-letter
#: suffix.
_SIZE_UNIT_MULTIPLIERS = (("GB", 1000**3), ("MB", 1000**2), ("kB", 1000), ("B", 1))


def _parse_docker_size(text: str) -> float:
    """Parse ``docker images``' human-readable size column (e.g. "670MB") into bytes.

    Deliberately *not* ``docker image inspect --format {{.Size}}``: on this
    Docker Desktop version (containerd image store) that field under-reports
    -- confirmed directly against the unmodified upstream ``python:3.13-slim``
    base image, where ``docker images`` reports 189MB but
    ``docker inspect --format {{.Size}}`` reports ~46MB for the exact same
    image. ``docker images``' column is what a human actually checking "how
    big is this image" sees, so it is the one this test measures.
    """
    text = text.strip()
    for unit, multiplier in _SIZE_UNIT_MULTIPLIERS:
        if text.endswith(unit):
            return float(text[: -len(unit)]) * multiplier
    raise ValueError(f"unrecognised docker size format: {text!r}")


@_requires_built_image
def test_image_size_is_at_most_700mb():
    result = subprocess.run(
        ["docker", "images", IMAGE_TAG, "--format", "{{.Size}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    size_bytes = _parse_docker_size(result.stdout.strip().splitlines()[0])
    assert size_bytes <= 700 * 1000 * 1000, (
        f"image is {size_bytes / 1_000_000:.1f} MB, over the 700 MB ceiling"
    )
