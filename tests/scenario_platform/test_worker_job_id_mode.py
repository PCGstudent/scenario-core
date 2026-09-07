"""Phase 3b's ``simulate --job-id`` path (Section 6.1 step 4): the worker reads
its job document from DynamoDB and its artifact from S3, then publishes
results back to S3 -- proven here against the SAME committed golden fixture
``tests/test_golden_fixture.py`` uses for the local-path Tier-1 replay
proof, so this test is also a Tier-1 replay proof for the AWS-shaped path.
"""

from __future__ import annotations

import json
from pathlib import Path

import boto3

from scenario_platform.adapters import job_store
from scenario_platform.worker.__main__ import main

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "gjr_skewt_v1"
GOLDEN_ARTIFACT_ID = (
    "sha256:fdf0b19144e0e899f29eb88aad30af4bb248deb7986ce34879d6cd00be29913f"
)
GOLDEN_MODEL_VERSION = "gjr-skewt-20260907-1"
GOLDEN_SCENARIO_DIGEST = (
    "sha256:637920e584b8e82449a67b84bfc39b73528256aa6518d8ca8280087b9fb3c255"
)


def _artifact_s3_prefix(artifact_id: str) -> str:
    return f"artifacts/{artifact_id.replace('sha256:', 'sha256_').replace(':', '_')}/"


def _seed_artifact_and_job(moto_env, job_id: str) -> None:
    s3 = boto3.client("s3", region_name=moto_env["region"])
    prefix = _artifact_s3_prefix(GOLDEN_ARTIFACT_ID)
    for name in ("artifact.json", "state.npz"):
        s3.upload_file(
            str(FIXTURE_DIR / name), moto_env["artifacts_bucket"], f"{prefix}{name}"
        )

    job_store._jobs_table().put_item(
        Item={
            "pk": f"JOB#{job_id}",
            "sk": "META",
            "job_id": job_id,
            "status": "RUNNING",
            "request": job_store.to_decimal(
                {
                    "artifact_id": GOLDEN_ARTIFACT_ID,
                    "model_version": GOLDEN_MODEL_VERSION,
                    "horizon": 252,
                    "n_paths": 1000,
                    "seed": 42,
                    "initial_state": "historical_mix",
                    "rng_scheme": "single",
                    "return_variance": False,
                    "risk_levels": [0.95, 0.99],
                }
            ),
        }
    )


def test_job_id_mode_matches_the_direct_local_path_bit_for_bit(moto_env):
    """Tier-1 equivalence between the AWS-shaped path and the already-proven
    local-path one (``tests/test_golden_fixture.py``), computed fresh in
    this same process/environment rather than compared against that other
    test's hard-coded historical digest -- the two tests would otherwise
    both depend on cross-environment/cross-session bit-reproducibility,
    which ``docker/worker.Dockerfile``'s own acceptance criteria (Phase 2,
    "Base-image BLAS and SIMD differences... where the README's documented
    fit drift becomes visible") already flags as a real, disclosed risk
    independent of anything Phase 3b changes. What Phase 3b actually needs
    to prove is narrower and environment-independent: that reading the
    identical artifact+request through S3/DynamoDB instead of local paths
    produces the identical array -- proven directly against a same-process
    local ``domain.services.simulate`` call below.
    """
    import hashlib

    from scenario_platform.domain import serialization
    from scenario_platform.domain.requests import ScenarioRequest
    from scenario_platform.domain.services import simulate as local_simulate

    local_artifact = serialization.load_artifact(FIXTURE_DIR)
    local_request = ScenarioRequest(
        model_version=GOLDEN_MODEL_VERSION, horizon=252, n_paths=1000, seed=42
    )
    local_digest = (
        "sha256:"
        + hashlib.sha256(
            local_simulate(local_artifact, local_request).returns.tobytes()
        ).hexdigest()
    )

    job_id = "job-golden-1"
    _seed_artifact_and_job(moto_env, job_id)

    exit_code = main(["simulate", "--job-id", job_id])
    assert exit_code == 0

    s3 = boto3.client("s3", region_name=moto_env["region"])
    manifest_body = s3.get_object(
        Bucket=moto_env["runs_bucket"], Key=f"runs/{job_id}/manifest.json"
    )["Body"].read()
    manifest = json.loads(manifest_body)
    assert manifest["returns_array_sha256"] == local_digest
    assert manifest["artifact_id"] == GOLDEN_ARTIFACT_ID

    # Manifest-last completion contract, proven over S3 exactly as it is
    # proven over the local filesystem in tests/test_golden_fixture.py.
    for name in ("returns.npy", "risk_report.json"):
        s3.head_object(Bucket=moto_env["runs_bucket"], Key=f"runs/{job_id}/{name}")


def test_job_id_mode_fails_closed_on_unknown_job(moto_env):
    exit_code = main(["simulate", "--job-id", "does-not-exist"])
    assert exit_code == 2  # ExitCode.INPUT


def test_job_id_mutually_exclusive_with_local_paths(moto_env, tmp_path):
    exit_code = main(
        [
            "simulate",
            "--job-id",
            "x",
            "--artifact-dir",
            str(tmp_path),
            "--request",
            str(tmp_path / "r.json"),
            "--output-dir",
            str(tmp_path / "out"),
        ]
    )
    assert exit_code == 2  # ExitCode.INPUT


def test_missing_all_arguments_is_input_error(moto_env):
    exit_code = main(["simulate"])
    assert exit_code == 2  # ExitCode.INPUT


def test_retried_job_id_invocation_does_not_crash_on_superseded_publish(moto_env):
    """Simulates a retried Fargate task for the SAME job (Section 6.3): the
    second `main()` call must still exit 0 -- the manifest write losing the
    race to the first invocation's is success, not an internal error."""
    job_id = "job-golden-retry"
    _seed_artifact_and_job(moto_env, job_id)

    first_exit = main(["simulate", "--job-id", job_id])
    assert first_exit == 0

    second_exit = main(["simulate", "--job-id", job_id])
    assert second_exit == 0
