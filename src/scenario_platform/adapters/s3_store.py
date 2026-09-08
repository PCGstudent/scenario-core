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


class ArtifactNotFound(Exception):
    """The artifact object genuinely does not exist -- Section 8.3's
    "artifact missing ... on the loader", mapped by
    the caller to ``ARTIFACT_INTEGRITY``. Never retried internally: a
    missing object does not become present by asking again."""


class ArtifactAccessConfigError(Exception):
    """The request was denied or misconfigured (wrong permissions, wrong
    endpoint policy, wrong credentials) -- NOT evidence the artifact
    itself is missing or corrupt, and must never be reported as
    ``ARTIFACT_INTEGRITY`` (Section 8.3's own language: that code
    "indicates storage mutation or a wrong-object read", neither of which
    is true here). Never retried internally: a permissions problem does
    not resolve itself between one call and the next a few seconds later.
    """


class ArtifactUnknownError(Exception):
    """Unclassified download failure: preserve the cause and fail closed."""


class ArtifactTransientError(Exception):
    """Every internal retry attempt (see :func:`download_artifact`) hit a
    transient-shaped S3/network condition (throttling, a 5xx, a connection
    timeout) and none of them succeeded. Distinct from both of the above:
    this is neither "missing" nor "misconfigured", it is "S3 was
    unavailable long enough to exhaust this worker's own retry budget."
    """


#: botocore ClientError codes worth a bounded internal retry before giving
#: up -- conditions that plausibly self-resolve within a few seconds, per
#: AWS's own general guidance for these exact codes. Never includes
#: NoSuchKey/NoSuchBucket (genuinely missing, retrying cannot help) or
#: AccessDenied-family codes (a permissions problem does not time-heal).
_TRANSIENT_CLIENT_ERROR_CODES = frozenset(
    {"SlowDown", "RequestTimeout", "InternalError", "ServiceUnavailable", "Throttling"}
)
_ARTIFACT_DOWNLOAD_MAX_ATTEMPTS = 3
_ARTIFACT_DOWNLOAD_RETRY_BACKOFF_SECONDS = 1.0


def _classify_download_error(exc: Exception) -> Exception:
    """Maps a raw boto3/botocore exception to one of the three classes
    above, preserving the original as ``__cause__`` either way (the caller
    always re-raises via ``raise ... from exc``, never swallowing it)."""
    from botocore.exceptions import (
        ClientError,
        ConnectionClosedError,
        ConnectTimeoutError,
        EndpointConnectionError,
        NoCredentialsError,
        PartialCredentialsError,
        ReadTimeoutError,
    )

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return ArtifactNotFound(str(exc))
        if code in (
            "NoSuchBucket",
            "AccessDenied",
            "AccessDeniedException",
            "403",
            "InvalidAccessKeyId",
            "SignatureDoesNotMatch",
        ):
            return ArtifactAccessConfigError(str(exc))
        if (
            code in _TRANSIENT_CLIENT_ERROR_CODES
            or exc.response.get("ResponseMetadata", {}).get("HTTPStatusCode", 0) >= 500
        ):
            return ArtifactTransientError(str(exc))
        return ArtifactUnknownError(str(exc))
    if isinstance(
        exc,
        (
            EndpointConnectionError,
            ConnectTimeoutError,
            ReadTimeoutError,
            ConnectionClosedError,
        ),
    ):
        return ArtifactTransientError(str(exc))
    if isinstance(exc, (NoCredentialsError, PartialCredentialsError)):
        return ArtifactAccessConfigError(str(exc))
    return ArtifactUnknownError(str(exc))


def download_artifact(artifact_id: str, dest_dir: Path) -> None:
    """Fetch a `load_artifact`-format directory from S3 into ``dest_dir``.

    Raises :class:`ArtifactNotFound`, :class:`ArtifactAccessConfigError` or
    :class:`ArtifactTransientError` -- never a raw ``ClientError`` -- so the
    caller (the worker's ``--job-id`` path) can map "genuinely missing" to
    ``ARTIFACT_INTEGRITY`` (Section 8.3) while letting a config/permissions
    problem or an exhausted transient retry surface as the unrelated
    failure it actually is, distinct from artifact tampering. Every raised
    exception chains the original via ``from exc`` -- the real cause is
    never discarded.

    Transient-shaped failures (see :data:`_TRANSIENT_CLIENT_ERROR_CODES`)
    are retried internally, with a short fixed backoff, up to
    :data:`_ARTIFACT_DOWNLOAD_MAX_ATTEMPTS` times before finally raising
    :class:`ArtifactTransientError` -- this is the one real effect on
    retry behaviour: a genuinely transient S3 blip can now self-heal
    within a single worker invocation instead of always burning one of
    Section 6.3's own ``MAX_ATTEMPTS`` Step-Functions-level retries for a
    problem this process could have absorbed on its own.
    """
    import time

    bucket = os.environ["ARTIFACTS_BUCKET"]
    prefix = artifact_prefix(artifact_id)
    dest_dir.mkdir(parents=True, exist_ok=True)

    last_exc: Exception | None = None
    for attempt in range(1, _ARTIFACT_DOWNLOAD_MAX_ATTEMPTS + 1):
        try:
            for name in _ARTIFACT_FILES:
                _client().download_file(bucket, f"{prefix}{name}", str(dest_dir / name))
            return
        except Exception as exc:  # noqa: BLE001 -- reclassified immediately below
            classified = _classify_download_error(exc)
            if not isinstance(classified, ArtifactTransientError):
                raise classified from exc
            last_exc = exc
            if attempt < _ARTIFACT_DOWNLOAD_MAX_ATTEMPTS:
                time.sleep(_ARTIFACT_DOWNLOAD_RETRY_BACKOFF_SECONDS)

    raise ArtifactTransientError(
        f"artifact {artifact_id!r} download failed after "
        f"{_ARTIFACT_DOWNLOAD_MAX_ATTEMPTS} attempts: {last_exc}"
    ) from last_exc


def _encryption_args() -> dict[str, str]:
    """Explicit headers required by the existing bucket policies."""
    return {
        "ServerSideEncryption": "aws:kms",
        "SSEKMSKeyId": os.environ["DATA_KMS_KEY_ARN"],
    }


def upload_artifact(artifact_id: str, source_dir: Path) -> None:
    """The registry-seeding counterpart to :func:`download_artifact` --
    used by ``scripts/seed_registry.py``, never by the worker or control
    plane at request time."""
    bucket = os.environ["ARTIFACTS_BUCKET"]
    prefix = artifact_prefix(artifact_id)
    for name in _ARTIFACT_FILES:
        _client().upload_file(
            str(source_dir / name), bucket, f"{prefix}{name}", ExtraArgs=_encryption_args()
        )


def runs_prefix(job_id: str) -> str:
    return f"runs/{job_id}/"


class ConcurrentPublishSuperseded(Exception):
    """Another invocation already committed this job's authoritative manifest."""


def upload_run_outputs(job_id: str, local_dir: Path) -> None:
    """Stage immutable attempt objects, then conditionally commit one manifest.

    Losing or interrupted attempts never write objects referenced by a winner.
    Unreferenced attempt objects remain for the runs bucket lifecycle to expire;
    the worker deliberately has no delete permission.
    """
    from uuid import uuid4

    from botocore.exceptions import ClientError

    manifest_path = local_dir / "manifest.json"
    if not manifest_path.is_file():
        return
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bucket = os.environ["RUNS_BUCKET"]
    prefix = runs_prefix(job_id)
    attempt = f"attempts/{uuid4().hex}/"
    outputs = {"returns": "returns.npy", "risk_report": "risk_report.json"}
    if (local_dir / "variances.npy").is_file():
        outputs["variances"] = "variances.npy"
    # Validate the complete local set before performing any remote writes.
    for name in outputs.values():
        if not (local_dir / name).is_file():
            raise ValueError(f"incomplete local result: missing {name}")
    for name in outputs.values():
        with (local_dir / name).open("rb") as body:
            _client().put_object(
                Bucket=bucket,
                Key=f"{prefix}{attempt}{name}",
                Body=body,
                IfNoneMatch="*",
                **_encryption_args(),
            )
    manifest["outputs"] = {key: attempt + name for key, name in outputs.items()}
    manifest["outputs"]["manifest"] = "manifest.json"
    try:
        _client().put_object(
            Bucket=bucket,
            Key=f"{prefix}manifest.json",
            Body=json.dumps(manifest, sort_keys=True).encode(),
            IfNoneMatch="*",
            **_encryption_args(),
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in ("PreconditionFailed", "412"):
            raise ConcurrentPublishSuperseded(
                f"job {job_id} already has a committed result"
            ) from exc
        raise


def _result_key(job_id: str, output: str) -> str | None:
    manifest = get_manifest(job_id)
    if manifest is None:
        return None
    relative = manifest.get("outputs", {}).get(output)
    if (
        not isinstance(relative, str)
        or relative.startswith("/")
        or any(part in ("", ".", "..") for part in relative.split("/"))
    ):
        raise ValueError(f"invalid manifest output {output!r}")
    return runs_prefix(job_id) + relative


def get_manifest(job_id: str) -> dict[str, Any] | None:
    """Read ``runs/{job_id}/manifest.json``, or ``None`` if it does not exist
    yet -- the caller (the ``jobs`` Lambda's GET .../results handler) uses
    this absence to mean "not complete", per the worker's own manifest-last
    completion contract (``docs/worker.md``)."""
    return _get_json(os.environ["RUNS_BUCKET"], f"{runs_prefix(job_id)}manifest.json")


def get_risk_report(job_id: str) -> dict[str, Any] | None:
    key = _result_key(job_id, "risk_report")
    return None if key is None else _get_json(os.environ["RUNS_BUCKET"], key)


def presigned_returns_url(job_id: str, *, expires_in: int = 900) -> str:
    """A presigned GET for ``returns.npy`` (Section 6.1 step 6: "15 min TTL").

    Inherits whatever IAM permissions the calling role has (Section 13.1's
    ``{env}-api-status`` note: "Presigned URLs inherit this role's
    permissions -- hence the narrow S3 scope") -- this function performs no
    permission check of its own; it is a pure wrapper over
    ``generate_presigned_url``.
    """
    bucket = os.environ["RUNS_BUCKET"]
    key = _result_key(job_id, "returns")
    if key is None:
        raise ValueError("job has no committed manifest")
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
