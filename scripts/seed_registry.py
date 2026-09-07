"""Seed one pre-registered, frozen, approved artifact into the model registry.

Phase 3b's own scope boundary (architecture plan Section 25): "against a
pre-registered frozen artifact" -- Phase 4's calibration/promotion workflow
(the calibrator role, the model-approver role, the real approval ceremony)
does not exist yet, on purpose. This script is the manual, disclosed stand-in
for that workflow, run once by a human with direct AWS credentials (never by
CI, never by a compute role) against a real DEV deployment:

1. Loads a local artifact directory (``scripts/build_artifact.py``'s output,
   or the committed Phase-1 fixture) and uploads it to
   ``s3://{artifacts_bucket}/artifacts/{artifact_id}/``.
2. Writes ``CANDIDATE#{family}#{model_version}``, ``APPROVAL#{family}#{model_version}``
   and ``POINTER#{family}/CURRENT`` directly into the model-registry table --
   the exact three item shapes Section 13.1's schema table defines, with this
   script's own operator identity recorded as ``approved_by`` in the
   candidate/approval records for an honest audit trail of how this
   particular pre-registration happened (a real Phase 4 approval records the
   same field for the same reason).

Never imported by application code, never run by CI, never given anything
but direct human credentials -- exactly the same posture
``infra/terraform/bootstrap/README.md`` documents for the first `terraform
apply`.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import boto3

from scenario_platform.domain.serialization import load_artifact


def seed(
    *,
    artifact_dir: Path,
    model_version: str,
    artifacts_bucket: str,
    registry_table: str,
    approved_by: str,
    region: str,
) -> None:
    artifact = load_artifact(artifact_dir)  # verifies its own identity on load
    family = artifact.family
    artifact_id = artifact.artifact_id

    s3 = boto3.client("s3", region_name=region)
    prefix = f"artifacts/{artifact_id.replace('sha256:', 'sha256_').replace(':', '_')}/"
    for name in ("artifact.json", "state.npz"):
        s3.upload_file(str(artifact_dir / name), artifacts_bucket, f"{prefix}{name}")
    print(f"Uploaded artifact to s3://{artifacts_bucket}/{prefix}")

    now = datetime.now(UTC).isoformat()
    table = boto3.resource("dynamodb", region_name=region).Table(registry_table)

    table.put_item(
        Item={
            "pk": f"CANDIDATE#{family}#{model_version}",
            "sk": "META",
            "artifact_id": artifact_id,
            "dataset_id": artifact.provenance.dataset_id,
            "threshold_set_version": artifact.threshold_set_version,
            "policy_set_version": artifact.policy_set_version,
            "created_at": now,
        }
    )
    table.put_item(
        Item={
            "pk": f"APPROVAL#{family}#{model_version}",
            "sk": "META",
            "artifact_id": artifact_id,
            "approved_by": approved_by,
            "approved_at": now,
            "acknowledged_failures": [],
            "justification": (
                "Phase 3b manual pre-registration (Section 25) -- no Phase 4 "
                "calibration/promotion workflow exists yet."
            ),
        }
    )
    table.put_item(
        Item={
            "pk": f"POINTER#{family}",
            "sk": "CURRENT",
            "model_version": model_version,
            "artifact_id": artifact_id,
            "updated_by": approved_by,
            "updated_at": now,
        }
    )
    print(f"Registered CANDIDATE#/APPROVAL#/POINTER# for {family}#{model_version}")
    print(f"artifact_id: {artifact_id}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifact-dir", type=Path, required=True)
    parser.add_argument("--model-version", required=True)
    parser.add_argument("--artifacts-bucket", required=True)
    parser.add_argument("--registry-table", required=True)
    parser.add_argument(
        "--approved-by", required=True, help="Your identity, for the audit trail."
    )
    parser.add_argument("--region", default="eu-west-1")
    args = parser.parse_args(argv)
    seed(
        artifact_dir=args.artifact_dir,
        model_version=args.model_version,
        artifacts_bucket=args.artifacts_bucket,
        registry_table=args.registry_table,
        approved_by=args.approved_by,
        region=args.region,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
