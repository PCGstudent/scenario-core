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
    """The artifact object genuinely does not exist (or the bucket does
    not) -- Section 8.3's "artifact missing ... on the loader", mapped by
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
    from botocore.exceptions import ClientError, EndpointConnectionError

    if isinstance(exc, ClientError):
        code = exc.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "NoSuchBucket", "404"):
            return ArtifactNotFound(str(exc))
        if code in (
            "AccessDenied",
            "AccessDeniedException",
            "403",
            "InvalidAccessKeyId",
            "SignatureDoesNotMatch",
        ):
            return ArtifactAccessConfigError(str(exc))
        if code in _TRANSIENT_CLIENT_ERROR_CODES:
            return ArtifactTransientError(str(exc))
        # An unrecognised ClientError code is treated as a config problem
        # rather than guessed as transient -- Section 6.3's own warning
        # against speculative classification applies here too: retrying an
        # error this function cannot actually place is more likely to burn
        # time on a permanent failure than to recover a real transient one.
        return ArtifactAccessConfigError(str(exc))
    if isinstance(exc, EndpointConnectionError):
        return ArtifactTransientError(str(exc))
    return ArtifactAccessConfigError(str(exc))


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


class ConcurrentPublishSuperseded(Exception):
    """Raised when this invocation's ``manifest.json`` write lost the race
    to an already-published, already-complete result at the same key --
    never treated as an error by the worker (Section 6.3: a re-run over the
    same job is deterministic, so whichever invocation's manifest actually
    got published names byte-identical content either way)."""


def upload_run_outputs(job_id: str, local_dir: Path) -> None:
    """Upload every file :func:`worker.__main__._write_outputs_atomically`
    already published in ``local_dir`` to ``runs/{job_id}/``.

    **Manifest-last is necessary but not sufficient.** Uploading files in
    manifest-last order (as the worker's own local-filesystem publish
    already does) prevents a consumer from ever seeing a manifest without
    its data -- but it does NOT, by itself, stop a SECOND invocation
    (a retried Fargate task after a transient failure, Section 6.3) from
    partially overwriting an already-complete result: both invocations can
    reach the upload step, both can start writing `returns.npy`/
    `risk_report.json` in some interleaved order, and "manifest exists"
    alone says nothing about which invocation's other files a reader
    actually sees alongside it.

    **The actual protection is S3's own conditional write on
    ``manifest.json``** (``IfNoneMatch: "*"`` -- "create only if this key
    does not already exist"): whichever invocation's ``PutObject`` for the
    manifest lands first wins and becomes the authoritative "this job is
    complete" marker; every other invocation's manifest write is rejected
    with ``PreconditionFailed`` (412), raised here as
    :class:`ConcurrentPublishSuperseded` rather than left as a raw
    ``ClientError`` -- a signal to the caller that ANOTHER invocation's
    result is the one now on record, not a failure of this invocation's
    own computation. Because a re-run over the same job is deterministic
    (Section 6.3: "rewrites identical bytes to the same keys"), the
    superseded invocation's own `returns.npy`/`risk_report.json` writes
    (already issued, non-conditional, before the manifest write) are
    content-identical to the winner's anyway -- so a reader is never
    exposed to a "wrong" result, only to whichever byte-identical copy
    happened to publish its manifest first.
    """
    bucket = os.environ["RUNS_BUCKET"]
    prefix = runs_prefix(job_id)
    data_files = [
        name
        for name in ("returns.npy", "risk_report.json", "variances.npy")
        if (local_dir / name).is_file()
    ]
    for name in data_files:
        _client().upload_file(str(local_dir / name), bucket, f"{prefix}{name}")

    manifest_path = local_dir / "manifest.json"
    if not manifest_path.is_file():
        return  # nothing to publish yet -- caller never reached the manifest-write step

    from botocore.exceptions import ClientError

    try:
        _client().put_object(
            Bucket=bucket,
            Key=f"{prefix}manifest.json",
            Body=manifest_path.read_bytes(),
            IfNoneMatch="*",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") == "PreconditionFailed":
            raise ConcurrentPublishSuperseded(
                f"runs/{job_id}/manifest.json was already published by another "
                "invocation; this invocation's own (content-identical) result was "
                "not the one recorded"
            ) from exc
        raise


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
