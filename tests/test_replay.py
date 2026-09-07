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
import os
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

# Overridable so the acceptance script (scripts/run_container_acceptance.sh)
# can point these tests at the exact immutable image ID it just built
# (`sha256:...`), not just whatever the mutable tag currently resolves to --
# `docker` accepts an image ID anywhere a tag is accepted, so no other
# change is needed for that to work.
IMAGE_TAG = os.environ.get("SCENARIO_WORKER_IMAGE", "scenario-core-worker:phase2")
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


# Thread pinning for this whole test session is set in tests/conftest.py, at
# import time, before numpy is first imported anywhere in the process -- see
# that file's docstring for why a per-test monkeypatch.setenv cannot work
# here (OpenBLAS reads these env vars once, at first load, not per-call).
# Every ordinary test below therefore runs with the same single-thread
# contract the production container's Dockerfile ENV provides, without
# needing its own fixture.


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
# 2b. Strict request validation: no coercion, no unknown fields, finite
#     numeric ranges, and policy handled deliberately (not as INTERNAL).
# ---------------------------------------------------------------------------


def _assert_request_rejected(tmp_path, request: dict[str, object]) -> str:
    request_path = tmp_path / "request.json"
    _write_request(request_path, request)
    exit_code, logs = _run_worker(
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
    assert exit_code == ExitCode.INPUT
    assert not (tmp_path / "output").exists()
    error_logs = [line for line in logs if line["level"] == "ERROR"]
    assert error_logs
    return str(error_logs[0]["error"])


def test_unknown_request_field_is_rejected(tmp_path):
    """A typo'd field name must fail loudly, not be silently ignored."""
    error = _assert_request_rejected(tmp_path, _golden_request(horizonn=252))
    assert "horizonn" in error


def test_fractional_horizon_is_rejected(tmp_path):
    """No int() coercion: 252.5 is not silently truncated to 252."""
    error = _assert_request_rejected(tmp_path, _golden_request(horizon=252.5))
    assert "horizon" in error


def test_string_n_paths_is_rejected(tmp_path):
    """No int() coercion: "1000" is not silently parsed."""
    error = _assert_request_rejected(tmp_path, _golden_request(n_paths="1000"))
    assert "n_paths" in error


def test_boolean_seed_is_rejected(tmp_path):
    """bool is an int subclass in Python -- must be explicitly excluded."""
    error = _assert_request_rejected(tmp_path, _golden_request(seed=True))
    assert "seed" in error


def test_negative_seed_is_rejected(tmp_path):
    error = _assert_request_rejected(tmp_path, _golden_request(seed=-1))
    assert "seed" in error


def test_zero_horizon_is_rejected(tmp_path):
    error = _assert_request_rejected(tmp_path, _golden_request(horizon=0))
    assert "horizon" in error


def test_zero_n_paths_is_rejected(tmp_path):
    error = _assert_request_rejected(tmp_path, _golden_request(n_paths=0))
    assert "n_paths" in error


def test_non_boolean_return_variance_is_rejected(tmp_path):
    """No bool() coercion: 1 is not silently accepted as True."""
    error = _assert_request_rejected(tmp_path, _golden_request(return_variance=1))
    assert "return_variance" in error


def test_non_finite_initial_state_value_is_rejected(tmp_path):
    error = _assert_request_rejected(
        tmp_path, _golden_request(initial_state=[float("nan"), 1.0])
    )
    assert "initial_state" in error


def test_non_positive_initial_state_variance_is_rejected(tmp_path):
    error = _assert_request_rejected(tmp_path, _golden_request(initial_state=[0.5, 0.0]))
    assert "variance" in error


def test_unsupported_initial_state_literal_is_rejected(tmp_path):
    error = _assert_request_rejected(tmp_path, _golden_request(initial_state="bogus_mode"))
    assert "initial_state" in error


def test_risk_level_at_or_beyond_boundary_is_rejected(tmp_path):
    """Strictly between 0 and 1 -- 1.0 itself is out of range."""
    error = _assert_request_rejected(tmp_path, _golden_request(risk_levels=[1.0]))
    assert "risk_levels" in error


def test_non_finite_risk_level_is_rejected(tmp_path):
    error = _assert_request_rejected(tmp_path, _golden_request(risk_levels=[float("inf")]))
    assert "risk_levels" in error


def test_policy_restricted_risk_level_is_rejected_as_input_not_internal(tmp_path):
    """A 99.9% tail-expectation risk level is restricted by Phase 1's own
    policy module (policies.check_metric_request) -- this must be handled
    deliberately as an ordinary INPUT rejection, never fall through to the
    generic internal-error handler as though it were unexpected."""
    error = _assert_request_rejected(tmp_path, _golden_request(risk_levels=[0.999]))
    assert "policy" in error.lower()


def test_policy_restricted_risk_level_is_permitted_with_governance(tmp_path):
    """The same restricted level, with an explicit governance cap, must
    succeed -- proving the rejection above is a real, working policy check
    and not a blanket ban on anything past 0.99."""
    request_path = tmp_path / "request.json"
    _write_request(
        request_path,
        _golden_request(
            risk_levels=[0.999],
            governance={"cap": "stress-review-2026", "approver": "risk-committee"},
        ),
    )
    exit_code, _logs = _run_worker(
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
    assert exit_code == ExitCode.SUCCESS


def test_return_variance_true_writes_variances_npy(tmp_path):
    """return_variance=true must produce a real variances.npy, not silently
    discard the requested output."""
    request_path = tmp_path / "request.json"
    _write_request(
        request_path, _golden_request(horizon=30, n_paths=10, return_variance=True)
    )
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
    assert (output_dir / "variances.npy").exists()
    variances = np.load(output_dir / "variances.npy")
    assert variances.shape == (10, 30)
    assert np.isfinite(variances).all()
    assert (variances > 0).all()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["outputs"]["variances"] == "variances.npy"
    assert manifest["variances_array_sha256"] is not None


def test_return_variance_false_writes_no_variances_npy(tmp_path):
    """The negative control for the test above: without return_variance,
    no variances.npy is written at all (not an empty/placeholder one)."""
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request(horizon=30, n_paths=10))
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
    assert not (output_dir / "variances.npy").exists()
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    assert "variances" not in manifest["outputs"]


# ---------------------------------------------------------------------------
# 2c. Artifact-loading boundary: every malformed-artifact exception is
#     normalised to ARTIFACT_INTEGRITY, narrowly, at that one boundary.
# ---------------------------------------------------------------------------


def _assert_artifact_rejected(tmp_path, artifact_dir: Path) -> None:
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    exit_code, logs = _run_worker(
        [
            "simulate",
            "--artifact-dir",
            str(artifact_dir),
            "--request",
            str(request_path),
            "--output-dir",
            str(tmp_path / "output"),
        ]
    )
    assert exit_code == ExitCode.ARTIFACT_INTEGRITY
    assert not (tmp_path / "output").exists()
    error_logs = [line for line in logs if line["level"] == "ERROR"]
    assert error_logs
    assert error_logs[0]["error_class"] == "ARTIFACT_INTEGRITY"


def test_invalid_json_artifact_metadata_is_artifact_integrity(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    (artifact_dir / "artifact.json").write_text("not json{{{", encoding="utf-8")
    _assert_artifact_rejected(tmp_path, artifact_dir)


def test_invalid_encoding_artifact_metadata_is_artifact_integrity(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    (artifact_dir / "artifact.json").write_bytes(b"\xff\xfe\x00not utf8")
    _assert_artifact_rejected(tmp_path, artifact_dir)


def test_missing_metadata_key_is_artifact_integrity(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    metadata_path = artifact_dir / "artifact.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    del data["params"]
    metadata_path.write_text(json.dumps(data), encoding="utf-8")
    _assert_artifact_rejected(tmp_path, artifact_dir)


def test_missing_dataclass_field_is_artifact_integrity(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    metadata_path = artifact_dir / "artifact.json"
    data = json.loads(metadata_path.read_text(encoding="utf-8"))
    del data["params"]["mu"]
    metadata_path.write_text(json.dumps(data), encoding="utf-8")
    _assert_artifact_rejected(tmp_path, artifact_dir)


def test_truncated_npz_is_artifact_integrity(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    state_path = artifact_dir / "state.npz"
    state_path.write_bytes(state_path.read_bytes()[:50])
    _assert_artifact_rejected(tmp_path, artifact_dir)


def test_empty_npz_is_artifact_integrity(tmp_path):
    artifact_dir = _copy_fixture(tmp_path)
    (artifact_dir / "state.npz").write_bytes(b"")
    _assert_artifact_rejected(tmp_path, artifact_dir)


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
# 3b. Runtime thread contract (direct lane): the worker must detect, not
#     just log, a numerical runtime that resolved to more than one thread.
# ---------------------------------------------------------------------------


def test_thread_contract_violation_is_detected_and_rejected(tmp_path):
    """Env-var overrides cannot be simulated mid-process -- OpenBLAS reads
    OMP_NUM_THREADS/OPENBLAS_NUM_THREADS/MKL_NUM_THREADS exactly once, at
    first load (see tests/conftest.py's docstring) -- so this uses
    threadpoolctl's own dynamic limiter (the same introspection layer the
    worker's ``_verify_thread_contract`` itself calls) to make the loaded
    runtime genuinely report >1 threads, proving detection independent of
    *why* the violation occurred. The container test below proves the
    specific ``docker run -e`` env-var-override scenario end-to-end, where
    env vars *do* still work (each container run is a fresh process).
    """
    import threadpoolctl

    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())

    with threadpoolctl.threadpool_limits(limits=4):
        exit_code, logs = _run_worker(
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
    assert exit_code == ExitCode.INTERNAL
    assert not (tmp_path / "output").exists()
    error_logs = [line for line in logs if line["level"] == "ERROR"]
    assert error_logs
    assert error_logs[0]["error_type"] == "WorkerThreadContractError"


# ---------------------------------------------------------------------------
# 3c. Publication mechanism (white-box): the CLI's own "--output-dir must be
# empty" precondition makes a pre-existing-unrelated-file scenario
# unreachable through main() (test_nonempty_output_directory_is_rejected
# above already proves that whole-request rejection preserves such a file);
# these tests instead call _write_outputs_atomically directly to prove the
# narrower, lower-level mechanism itself -- rollback on a handled failure,
# and the single-writer lock -- exactly as documented in that function's
# own docstring. Deliberately below the CLI boundary: these are internal
# mechanisms with no other way to construct the scenario.
# ---------------------------------------------------------------------------


def _minimal_risk_report():
    from scenario_platform.domain.reports import RiskReport

    return RiskReport(
        artifact_id=GOLDEN_ARTIFACT_ID,
        horizon=10,
        n_paths=4,
        var_es={0.95: (1.0, 1.5)},
        max_drawdown_median=0.1,
    )


def test_failure_during_staging_leaves_no_manifest_and_preserves_unrelated_files(
    tmp_path, monkeypatch
):
    """A failure while *staging* (before any file has even been published
    into ``output_dir``) must still leave the pre-existing unrelated file
    alone and publish nothing at all."""
    import scenario_platform.worker.__main__ as worker_main

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "unrelated.txt").write_text("keep me", encoding="utf-8")

    real_write_text = Path.write_text

    def flaky_write_text(self, *args, **kwargs):
        if self.name == "risk_report.json":
            raise OSError("simulated staging failure")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", flaky_write_text)

    with pytest.raises(OSError, match="simulated staging failure"):
        worker_main._write_outputs_atomically(
            output_dir,
            b"\x93NUMPY fake returns bytes",
            _minimal_risk_report(),
            {"schema_version": "test"},
            None,
        )

    assert (output_dir / "unrelated.txt").read_text(encoding="utf-8") == "keep me"
    assert not (output_dir / "manifest.json").exists()
    assert not (output_dir / "returns.npy").exists()
    remaining = {p.name for p in output_dir.iterdir()}
    assert remaining == {"unrelated.txt"}


def test_failure_between_file_publications_rolls_back_and_preserves_unrelated_files(
    tmp_path, monkeypatch
):
    """returns.npy publishes successfully; risk_report.json's publish is
    then made to fail. Must prove: returns.npy is rolled back (no partial
    result), manifest.json never appears, and the pre-existing unrelated
    file is untouched."""
    import scenario_platform.worker.__main__ as worker_main

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / "unrelated.txt").write_text("keep me", encoding="utf-8")

    real_replace = worker_main.os.replace
    calls = {"n": 0}

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:  # the second publish call: risk_report.json
            raise OSError("simulated failure between file publications")
        return real_replace(src, dst)

    monkeypatch.setattr(worker_main.os, "replace", flaky_replace)

    with pytest.raises(OSError, match="simulated failure between file publications"):
        worker_main._write_outputs_atomically(
            output_dir,
            b"\x93NUMPY fake returns bytes",
            _minimal_risk_report(),
            {"schema_version": "test"},
            None,
        )

    assert (output_dir / "unrelated.txt").read_text(encoding="utf-8") == "keep me"
    assert not (output_dir / "manifest.json").exists()
    assert not (output_dir / "returns.npy").exists()  # rolled back
    assert not (output_dir / "risk_report.json").exists()
    remaining = {p.name for p in output_dir.iterdir()}
    assert remaining == {"unrelated.txt"}


def test_concurrent_publish_to_the_same_output_dir_is_rejected(tmp_path):
    """Enforces the single-writer assumption: a lock file already present
    (as if another invocation's _write_outputs_atomically were mid-flight)
    must cause an immediate, atomic rejection -- not a race where both
    writers proceed and interleave their files."""
    from scenario_platform.worker.__main__ import _write_outputs_atomically
    from scenario_platform.worker.errors import WorkerConcurrentPublishError

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    (output_dir / ".worker-lock").touch()

    with pytest.raises(WorkerConcurrentPublishError):
        _write_outputs_atomically(
            output_dir,
            b"\x93NUMPY fake returns bytes",
            _minimal_risk_report(),
            {"schema_version": "test"},
            None,
        )
    # The pre-existing lock file (simulating the other writer's) is left
    # alone -- this call never owned it, so it must not remove it.
    assert (output_dir / ".worker-lock").exists()


# ---------------------------------------------------------------------------
# 3d. Digest terminology: array-bytes digest vs. complete-.npy-file digest
#     are genuinely different values, never confused.
# ---------------------------------------------------------------------------


def test_array_digest_and_npy_file_digest_are_different_and_both_correct(tmp_path):
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
    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))

    array_digest = manifest["returns_array_sha256"]
    file_digest = manifest["returns_npy_file_sha256"]
    assert array_digest != file_digest  # the .npy header makes them genuinely different

    # The array digest -- and only the array digest -- is the one ever
    # compared against the Phase-1 golden value.
    assert array_digest == GOLDEN_SCENARIO_DIGEST

    returns = np.load(output_dir / "returns.npy")
    recomputed_array_digest = (
        "sha256:"
        + __import__("hashlib").sha256(np.ascontiguousarray(returns).tobytes()).hexdigest()
    )
    assert recomputed_array_digest == array_digest

    recomputed_file_digest = (
        "sha256:"
        + __import__("hashlib")
        .sha256((output_dir / "returns.npy").read_bytes())
        .hexdigest()
    )
    assert recomputed_file_digest == file_digest


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
    # ``docker images <ref>``'s positional filter only matches a
    # repository[:tag] reference, never an image ID -- it silently returns
    # no rows (confirmed directly) when IMAGE_TAG is instead a raw
    # "sha256:..." ID, which SCENARIO_WORKER_IMAGE deliberately sets it to
    # for acceptance runs (see scripts/run_container_acceptance.sh), so
    # that this suite tests the exact immutable image just built rather
    # than whatever a mutable tag happens to currently point at. Listing
    # every image with --no-trunc and matching the full ID text works for
    # both a tag and an ID.
    result = subprocess.run(
        ["docker", "images", "--no-trunc", "--format", "{{.ID}} {{.Size}}"],
        capture_output=True,
        text=True,
        check=True,
    )
    matches = [
        line.split(" ", 1)[1]
        for line in result.stdout.strip().splitlines()
        if line.split(" ", 1)[0] in (IMAGE_TAG, f"sha256:{IMAGE_TAG}")
    ]
    if not matches:
        # Fall back to a direct tag/reference filter for the common case of
        # a human tag such as "scenario-core-worker:phase2".
        tag_result = subprocess.run(
            ["docker", "images", IMAGE_TAG, "--format", "{{.Size}}"],
            capture_output=True,
            text=True,
            check=True,
        )
        matches = tag_result.stdout.strip().splitlines()
    assert matches, f"no `docker images` row found for {IMAGE_TAG!r}"
    size_bytes = _parse_docker_size(matches[0])
    assert size_bytes <= 700 * 1000 * 1000, (
        f"image is {size_bytes / 1_000_000:.1f} MB, over the 700 MB ceiling"
    )


@_requires_built_image
def test_container_actual_thread_counts_are_single_threaded(tmp_path):
    """Not the env vars -- the *actual* threadpoolctl-reported thread pools
    inside the container, matching the check the worker itself performs."""
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
            "-c",
            "import numpy, scipy, threadpoolctl, json; "
            "print(json.dumps(threadpoolctl.threadpool_info()))",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    pools = json.loads(result.stdout.strip().splitlines()[-1])
    assert pools, "expected at least one BLAS/OpenMP pool to be reported"
    for pool in pools:
        assert pool["num_threads"] == 1, pool


@_requires_built_image
def test_container_runtime_thread_override_cannot_silently_violate_the_contract(tmp_path):
    """The specific ``docker run -e`` scenario: a caller overrides the
    Dockerfile's baked-in ``OMP_NUM_THREADS=1`` at container-start time.
    Because each ``docker run`` is a fresh process, the override *does* take
    effect (unlike the in-process direct-lane test above) -- and the worker
    must detect it and fail closed rather than silently computing
    multi-threaded."""
    request_path = tmp_path / "request.json"
    _write_request(request_path, _golden_request())
    output_dir = tmp_path / "output"
    output_dir.mkdir(parents=True, exist_ok=True)

    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--platform",
            "linux/amd64",
            "-e",
            "OMP_NUM_THREADS=4",
            "-e",
            "OPENBLAS_NUM_THREADS=4",
            "-v",
            f"{FIXTURE_DIR}:/artifact:ro",
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
    assert result.returncode == ExitCode.INTERNAL, result.stdout + result.stderr
    assert "WorkerThreadContractError" in result.stdout
    assert not any(output_dir.iterdir())


@_requires_built_image
def test_container_cold_import_time_is_recorded():
    """Records (does not gate on) the time to import the worker's entry
    point module inside the container -- a budget per Phase 2's own Files
    bullet ("cold-import time recorded as budgets"), printed for the
    acceptance script/report to pick up rather than asserted against an
    arbitrary threshold this test would otherwise have to invent."""
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
            "-c",
            "import time; t0 = time.monotonic(); "
            "import scenario_platform.worker.__main__; "
            "print(f'cold_import_seconds={time.monotonic() - t0:.3f}')",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    line = next(
        line
        for line in result.stdout.splitlines()
        if line.startswith("cold_import_seconds=")
    )
    seconds = float(line.split("=", 1)[1])
    print(f"\ncold-import time: {seconds:.3f}s")
    assert seconds > 0
