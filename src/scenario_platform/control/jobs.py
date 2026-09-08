"""``GET /scenario-jobs/{job_id}``, ``GET .../results``, ``DELETE /scenario-jobs/{job_id}``
(architecture plan Section 6.1 steps 5-7).

One Lambda handles all three routes -- matching Section 13.1's single
``{env}-api-status`` role, which carries exactly the union of permissions
these three need (``dynamodb:GetItem,Query``, ``s3:GetObject`` on
``runs/*``, ``states:StopExecution``).
"""

from __future__ import annotations

from typing import Any

import boto3

from ..adapters import job_store, s3_store
from ..adapters.logging import configure_logging
from .errors import HandlerError, error_response, json_response, principal_arn

LOGGER = configure_logging("scenario_platform.control.jobs")

_sfn_client: Any = None


def _sfn() -> Any:
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client


def _job_or_404(job_id: str) -> dict[str, Any]:
    job = job_store.get_job(job_id)
    if job is None:
        raise HandlerError(404, "NotFound", f"no job {job_id!r}")
    return job


def _get_status(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    return json_response(
        200,
        {
            "job_id": job_id,
            "status": job.get("status"),
            # DynamoDB always returns numbers as Decimal (job_store.to_native's
            # own docstring) -- converted back to plain int/float here so the
            # HTTP response carries real JSON numbers, not stringified ones.
            "request": job_store.to_native(job.get("request")),
            "provenance": job_store.to_native(job.get("provenance")),
            "attempts": job_store.to_native(job.get("attempts", [])),
        },
    )


def _get_results(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    status = job.get("status")
    if status != "SUCCEEDED":
        raise HandlerError(
            409, "JobNotComplete", f"job {job_id!r} is {status!r}, not SUCCEEDED"
        )
    manifest = s3_store.get_manifest(job_id)
    risk_report = s3_store.get_risk_report(job_id)
    if manifest is None:
        # Section 6.3's own invariant: SUCCEEDED implies the manifest exists.
        # If it does not, the job record and S3 have diverged -- report it
        # as an internal error rather than a 404 a client might retry into.
        raise HandlerError(
            500, "InternalError", f"job {job_id!r} is SUCCEEDED but has no manifest.json"
        )
    return json_response(
        200,
        {
            "job_id": job_id,
            "manifest": manifest,
            "risk_report": risk_report,
            "returns_url": s3_store.presigned_returns_url(job_id, expires_in=900),
        },
    )


def _delete(job_id: str) -> dict[str, Any]:
    job = _job_or_404(job_id)
    status = job.get("status")
    if status in job_store.TERMINAL_STATUSES - {"CANCELLED"}:
        return json_response(200, {"job_id": job_id, "status": status})
    # Commit cancellation before stopping the execution. A retry must still
    # attempt StopExecution if a previous call failed after this write.
    final_status = job_store.mark_cancelled(job_id)
    if final_status == "CANCELLED":
        current = _job_or_404(job_id)
        execution_arn = current.get("execution_arn")
        if execution_arn:
            try:
                _sfn().stop_execution(executionArn=execution_arn)
            except _sfn().exceptions.ExecutionDoesNotExist:
                pass
    return json_response(200, {"job_id": job_id, "status": final_status})


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    try:
        principal_arn(event)  # defense-in-depth auth check; see submit.py's docstring
        job_id = (event.get("pathParameters") or {}).get("job_id")
        if not job_id:
            raise HandlerError(400, "BadRequest", "missing job_id path parameter")

        method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
        raw_path = event.get("rawPath", "")

        if method == "DELETE":
            return _delete(job_id)
        if method == "GET" and raw_path.endswith("/results"):
            return _get_results(job_id)
        if method == "GET":
            return _get_status(job_id)
        raise HandlerError(405, "MethodNotAllowed", f"unsupported method {method!r}")
    except HandlerError as exc:
        return error_response(exc)
    except Exception:  # noqa: BLE001
        LOGGER.exception("jobs handler failed with an unexpected error")
        return error_response(HandlerError(500, "InternalError", "unexpected error"))
