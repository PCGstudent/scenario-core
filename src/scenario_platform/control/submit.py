"""``POST /scenario-jobs`` (architecture plan Section 6.1 step 2, Section 6.2/6.2a).

Lambda entry point: ``handler(event, context)``, API Gateway HTTP API
(payload format 2.0) with ``AWS_IAM`` authorization. Implements the full
submit algorithm in order: authn context, schema validation, the
idempotency fast path (Section 6.2), admission (Section 6.1 step 2e),
model-version resolution and the approval predicate (Section 8.4),
governance/policy (Section 8.5), seed assignment, atomic persistence
(Section 6.2a), and ``StartExecution`` with healing (Section 6.2a).

**Execution-ARN construction, not recovery-by-lookup.** Section 6.2a's
healing path suggests recovering a lost ``execution_arn`` via
``DescribeExecution``/``ListExecutions``. This implementation instead
constructs the ARN directly: a Step Functions execution ARN is
``arn:aws:states:{region}:{account}:execution:{state_machine_name}:{name}``,
entirely deterministic once ``name = job_id`` is fixed, so there is nothing
to look up -- the ARN is knowable before ``StartExecution`` is even called,
and ``ExecutionAlreadyExists`` from a healing retry changes nothing about
it. This is a strictly more reliable simplification of the documented
mechanism, not a deviation from its outcome.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from datetime import UTC, datetime
from typing import Any

import boto3
from pydantic import ValidationError

from ..adapters import job_store
from ..adapters.logging import configure_logging
from . import admission
from .errors import HandlerError, error_response, json_response, principal_arn
from .hashing import client_request_hash
from .schemas import ScenarioJobIn, ScenarioJobOut

LOGGER = configure_logging("scenario_platform.control.submit")

_sfn_client: Any = None


def _sfn() -> Any:
    global _sfn_client
    if _sfn_client is None:
        _sfn_client = boto3.client("stepfunctions")
    return _sfn_client


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _get_header(event: dict[str, Any], name: str) -> str | None:
    headers = event.get("headers") or {}
    # API Gateway HTTP API lower-cases header names in payload format 2.0,
    # but this is defensive rather than relied upon.
    for key, value in headers.items():
        if key.lower() == name.lower():
            return str(value)
    return None


def _execution_arn_for(job_id: str) -> tuple[str, str]:
    """Returns ``(state_machine_arn, execution_arn)``, both derived from
    ``STATE_MACHINE_ARN`` -- see module docstring."""
    state_machine_arn = os.environ["STATE_MACHINE_ARN"]
    # arn:aws:states:{region}:{account}:stateMachine:{name}
    _, _, _, region, account, _, name = state_machine_arn.split(":", 6)
    execution_arn = f"arn:aws:states:{region}:{account}:execution:{name}:{job_id}"
    return state_machine_arn, execution_arn


def _start_or_heal_execution(job_id: str) -> str:
    """Starts (or recovers, per the module docstring) the execution, then
    returns the job's CURRENT status -- which is not necessarily ``QUEUED``:

    * The state machine's own ``RecordQueued``/``RecordSucceeded`` native
      updates can land before this function's own status write does (a
      fast job can finish before this synchronous caller even gets here);
      ``job_store.set_execution_arn`` never regresses that, and this
      function reports whatever it actually observes.
    * A ``DELETE`` can race a job that is still ``SUBMITTED`` with no
      ``execution_arn`` yet (cancellation during startup): the caller wins,
      marks it ``CANCELLED``, and only THEN does this function's
      ``StartExecution``/``set_execution_arn`` sequence run. Detected here
      by ``set_execution_arn`` reporting ``CANCELLED`` back -- in which case
      the just-started (or just-recovered) execution is itself stopped
      immediately, best-effort, so a cancelled job does not silently keep
      running compute anyway.
    """
    deadline = os.environ.get("DEMO_DEADLINE_UTC")
    if deadline and datetime.now(UTC) >= datetime.fromisoformat(
        deadline.replace("Z", "+00:00")
    ):
        raise HandlerError(503, "DemoWindowClosed", "the demonstration window has ended")
    state_machine_arn, execution_arn = _execution_arn_for(job_id)
    try:
        _sfn().start_execution(
            stateMachineArn=state_machine_arn,
            name=job_id,
            input=json.dumps({"job_id": job_id}),
        )
    except _sfn().exceptions.ExecutionAlreadyExists:
        pass
    status = job_store.set_execution_arn(job_id, execution_arn)
    if status == "CANCELLED":
        try:
            _sfn().stop_execution(executionArn=execution_arn)
        except _sfn().exceptions.ExecutionDoesNotExist:
            pass
        LOGGER.info(
            "execution stopped: job was cancelled before/during startup",
            extra={"job_id": job_id, "execution_arn": execution_arn},
        )
    return status


def _canonical_request(
    request: ScenarioJobIn,
    *,
    resolved_model_version: str,
    resolved_artifact_id: str,
    seed: int,
) -> dict[str, Any]:
    return {
        "model_version": resolved_model_version,
        "artifact_id": resolved_artifact_id,
        "horizon": request.horizon,
        "n_paths": request.n_paths,
        "seed": seed,
        "initial_state": (
            list(request.initial_state)
            if isinstance(request.initial_state, tuple)
            else request.initial_state
        ),
        "rng_scheme": request.rng_scheme,
        "return_variance": request.include_variance,
        "risk_levels": request.metrics,
        "governance": request.governance.model_dump() if request.governance else None,
    }


def _handle_replay(idem: dict[str, Any], expected_hash: str) -> ScenarioJobOut:
    """Section 6.2's replay table, plus Section 6.2a healing when the
    original job never reached ``QUEUED``."""
    if idem["client_request_hash"] != expected_hash:
        raise HandlerError(
            409,
            "IdempotencyConflict",
            "this Idempotency-Key was already used with a different request",
        )
    job_id = str(idem["job_id"])
    job = job_store.get_job(job_id)
    if job is None:
        raise HandlerError(
            500, "InternalError", "idempotency record exists but its job item is missing"
        )
    status = str(job["status"])
    if status == "SUBMITTED" and "execution_arn" not in job:
        status = _start_or_heal_execution(job_id)
    return ScenarioJobOut(job_id=job_id, status=status, poll=f"/scenario-jobs/{job_id}")


def _submit_new_job(
    request: ScenarioJobIn, *, principal: str, idempotency_key: str | None, req_hash: str
) -> dict[str, Any]:
    deadline = os.environ.get("DEMO_DEADLINE_UTC")
    if deadline and datetime.now(UTC) >= datetime.fromisoformat(
        deadline.replace("Z", "+00:00")
    ):
        raise HandlerError(503, "DemoWindowClosed", "the demonstration window has ended")
    admission.check_admission(request)
    resolved_version, resolved_artifact_id = admission.resolve_and_authorize(
        request.model_version
    )
    admission.check_policy(request)

    assigned_seed = request.seed if request.seed is not None else secrets.randbits(31)
    canonical_request = _canonical_request(
        request,
        resolved_model_version=resolved_version,
        resolved_artifact_id=resolved_artifact_id,
        seed=assigned_seed,
    )
    provenance = {"submitted_at": _now_iso(), "principal": principal}
    job_id = str(uuid.uuid4())

    for attempt in range(2):
        new_job = job_store.NewJob(
            job_id=job_id, canonical_request=canonical_request, provenance=provenance
        )
        try:
            job_store.submit_job(
                new_job,
                principal=principal,
                idempotency_key=idempotency_key,
                client_request_hash=req_hash if idempotency_key else None,
                assigned_seed=assigned_seed if idempotency_key else None,
                resolved_model_version=resolved_version if idempotency_key else None,
                resolved_artifact_id=resolved_artifact_id if idempotency_key else None,
            )
            break
        except job_store.IdempotencyConflict:
            assert idempotency_key is not None
            idem = job_store.get_idem_record(principal, idempotency_key)
            if idem is None:
                raise HandlerError(
                    500,
                    "InternalError",
                    "idempotency race lost but record missing on re-read",
                ) from None
            out = _handle_replay(idem, req_hash)
            return json_response(200, out.model_dump())
        except job_store.JobIdCollision:
            if attempt == 1:
                LOGGER.error("job_id collision recurred", extra={"job_id": job_id})
                raise HandlerError(
                    500, "InternalError", "job_id collision recurred"
                ) from None
            job_id = str(uuid.uuid4())

    status = _start_or_heal_execution(job_id)
    out = ScenarioJobOut(job_id=job_id, status=status, poll=f"/scenario-jobs/{job_id}")
    return json_response(202, out.model_dump())


def handler(event: dict[str, Any], context: Any = None) -> dict[str, Any]:
    try:
        principal = principal_arn(event)
        try:
            body = json.loads(event.get("body") or "{}")
            request = ScenarioJobIn.model_validate(body)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise HandlerError(422, "ValidationError", str(exc)) from exc

        idempotency_key = _get_header(event, "idempotency-key")
        req_hash = client_request_hash(request)

        if idempotency_key is not None:
            idem = job_store.get_idem_record(principal, idempotency_key)
            if idem is not None:
                out = _handle_replay(idem, req_hash)
                return json_response(200, out.model_dump())

        return _submit_new_job(
            request, principal=principal, idempotency_key=idempotency_key, req_hash=req_hash
        )
    except HandlerError as exc:
        return error_response(exc)
    except Exception:  # noqa: BLE001 -- last-resort 500, never a raw Lambda crash
        LOGGER.exception("submit handler failed with an unexpected error")
        return error_response(HandlerError(500, "InternalError", "unexpected error"))
