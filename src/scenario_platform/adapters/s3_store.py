"""S3 access for frozen artifacts (``artifacts/*``) and run outputs (``runs/*``).

Architecture plan Section 6.1 step 4a/4f, Section 13.1's ``{env}-worker-task``
grant (``s3:GetObject`` on ``artifacts/*``, ``s3:PutObject`` on ``runs/*``).
The other half of "the ONLY place boto3 appears in the data plane" alongside
``job_store.py`` -- see that module's docstring for the full rationale.

Bucket names come from environment variables (``ARTIFACTS_BUCKET``,
``RUNS_BUCKET``), never hard-coded.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, cast

import boto3

_s3_client: Any = None


def _client() -> Any:
    global _s3_client
    if _s3_client is None:
        _s3_client = boto3.client("s3")
    return _s3_client


#: The exact two files a `load_artifact`-format directory holds
#: (`scenario_platform.domain.serialization`) -- never a third, never fewer.
_ARTIFACT_FILES: tuple[str, ...] = ("artifact.json", "state.npz")


def artifact_prefix(artifact_id: str) -> str:
    """``artifacts/{artifact_id}/`` -- ``artifact_id`` already carries a
    ``sha256:`` prefix and a colon, neither of which is a valid S3 key
    character to leave unescaped; both are replaced with ``_`` for the key
    alone (never for the value compared against ``ModelArtifact.artifact_id``,
    which is untouched)."""
    safe = artifact_id.replace("sha256:", "sha256_").replace(":", "_")
    return f"artifacts/{safe}/"


def download_artifact(artifact_id: str, dest_dir: Path) -> None:
    """Fetch a `load_artifact`-format directory from S3 into ``dest_dir``.

    Raises whatever ``botocore`` raises (typically ``ClientError`` with code
    ``NoSuchKey``/``404``) on a missing object -- the caller (the worker's
    ``--job-id`` path) maps that to ``ARTIFACT_INTEGRITY``, matching Section
    8.3's "artifact missing ... on the loader" fail-closed rule exactly as
    the existing local-path loader already does for a missing directory.
    """
    bucket = os.environ["ARTIFACTS_BUCKET"]
    prefix = artifact_prefix(artifact_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    for name in _ARTIFACT_FILES:
        _client().download_file(bucket, f"{prefix}{name}", str(dest_dir / name))


def upload_artifact(artifact_id: str, source_dir: Path) -> None:
    """The registry-seeding counterpart to :func:`download_artifact` --
    used by ``scripts/seed_registry.py``, never by the worker or control
    plane at request time."""
    bucket = os.environ["ARTIFACTS_BUCKET"]
    prefix = artifact_prefix(artifact_id)
    for name in _ARTIFACT_FILES:
        _client().upload_file(str(source_dir / name), bucket, f"{prefix}{name}")


def runs_prefix(job_id: str) -> str:
    return f"runs/{job_id}/"


def upload_run_outputs(job_id: str, local_dir: Path) -> None:
    """Upload every file :func:`worker.__main__._write_outputs_atomically`
    already published in ``local_dir`` to ``runs/{job_id}/``, in the same
    manifest-last order the worker itself uses -- a consumer of
    :func:`get_manifest` must see the same "manifest present means complete"
    guarantee in S3 that the local filesystem contract already provides.
    """
    bucket = os.environ["RUNS_BUCKET"]
    prefix = runs_prefix(job_id)
    ordered = [
        name
        for name in ("returns.npy", "risk_report.json", "variances.npy", "manifest.json")
        if (local_dir / name).is_file()
    ]
    for name in ordered:
        _client().upload_file(str(local_dir / name), bucket, f"{prefix}{name}")


def get_manifest(job_id: str) -> dict[str, Any] | None:
    """Read ``runs/{job_id}/manifest.json``, or ``None`` if it does not exist
    yet -- the caller (the ``jobs`` Lambda's GET .../results handler) uses
    this absence to mean "not complete", per the worker's own manifest-last
    completion contract (``docs/worker.md``)."""
    return _get_json(os.environ["RUNS_BUCKET"], f"{runs_prefix(job_id)}manifest.json")


def get_risk_report(job_id: str) -> dict[str, Any] | None:
    return _get_json(os.environ["RUNS_BUCKET"], f"{runs_prefix(job_id)}risk_report.json")


def presigned_returns_url(job_id: str, *, expires_in: int = 900) -> str:
    """A presigned GET for ``returns.npy`` (Section 6.1 step 6: "15 min TTL").

    Inherits whatever IAM permissions the calling role has (Section 13.1's
    ``{env}-api-status`` note: "Presigned URLs inherit this role's
    permissions -- hence the narrow S3 scope") -- this function performs no
    permission check of its own; it is a pure wrapper over
    ``generate_presigned_url``.
    """
    bucket = os.environ["RUNS_BUCKET"]
    key = f"{runs_prefix(job_id)}returns.npy"
    return str(
        _client().generate_presigned_url(
            "get_object", Params={"Bucket": bucket, "Key": key}, ExpiresIn=expires_in
        )
    )


def _get_json(bucket: str, key: str) -> dict[str, Any] | None:
    from botocore.exceptions import ClientError

    try:
        body = _client().get_object(Bucket=bucket, Key=key)["Body"].read()
    except ClientError as exc:
        code = exc.response.get("Error", {}).get("Code")
        if code in ("NoSuchKey", "404"):
            return None
        raise
    return cast(dict[str, Any], json.loads(body))
