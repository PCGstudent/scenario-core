"""DynamoDB access for the scenario-jobs and model-registry tables.

Architecture plan Section 6.1/6.2/6.2a (job table) and Section 8.4 (registry
table). This module -- together with ``s3_store.py`` -- is "the ONLY place
boto3 appears in the data plane" (Section 22's repository-structure comment):
``scenario_platform.domain`` and ``scenario_platform.worker`` never import
boto3 directly (``tests/test_no_aws_in_core.py``); both call into this module
instead. ``scenario_platform.control`` (a different plane) also calls this
module rather than talking to DynamoDB inline, so the item shapes below are
defined exactly once.

Table names come from environment variables the Lambda/task definitions set
(``SCENARIO_JOBS_TABLE``, ``MODEL_REGISTRY_TABLE``) -- never hard-coded,
never guessed from ``ENVIRONMENT``, so this module has no notion of "dev" or
"prod" of its own.

Every function here is a thin wrapper over one DynamoDB call (or, for
``submit_job``, one transaction) -- no retry policy, no caching, no business
logic beyond the item-shape/key-schema translation the architecture plan
specifies. Admission, idempotency-hash comparison and the approval predicate
itself live in ``scenario_platform.control``, which calls these functions and
decides what the results mean.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Any, cast

import boto3
from botocore.exceptions import ClientError

_dynamodb_resource: Any = None
_dynamodb_client: Any = None


def _client() -> Any:
    """A plain low-level client -- distinct from ``_resource().meta.client``
    on purpose. boto3's DynamoDB *resource* attaches its own attribute-value
    injection/transform handlers to its underlying client (so
    ``Table.put_item``/``get_item`` can accept native Python types); those
    handlers also fire on ``transact_write_items`` when called through
    ``resource.meta.client``, and re-process an ``Item`` this module has
    ALREADY hand-serialized into raw ``{"S": ...}``-shaped `AttributeValue`
    dicts (required for that low-level API, which the resource does not
    wrap) -- confirmed directly: identical item content succeeds through a
    plain ``boto3.client("dynamodb")`` and fails through
    ``boto3.resource("dynamodb").meta.client`` with the same error every
    time. ``transact_write_items`` therefore always goes through this
    client, never through the resource.
    """
    global _dynamodb_client
    if _dynamodb_client is None:
        _dynamodb_client = boto3.client("dynamodb")
    return _dynamodb_client


def _resource() -> Any:
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource


def _jobs_table() -> Any:
    return _resource().Table(os.environ["SCENARIO_JOBS_TABLE"])


def _registry_table() -> Any:
    return _resource().Table(os.environ["MODEL_REGISTRY_TABLE"])


class IdempotencyConflict(Exception):
    """The ``IDEM#`` write lost a race (Section 6.2's ``TransactionCanceledException`` row).

    The caller must re-read the ``IDEM#`` item and behave exactly as a normal
    replay -- this exception carries no state of its own beyond the fact that
    a re-read is now required.
    """


class JobIdCollision(Exception):
    """The ``JOB#`` write lost a race under a fresh (no-idempotency-key) submission.

    Section 6.2's table: "A UUIDv4 collision. Regenerate job_id, retry once,
    alarm if it recurs." This process (not this module) owns the retry.
    """


# --- scenario-jobs table: IDEM# / JOB# -------------------------------------


def get_idem_record(principal: str, idempotency_key: str) -> dict[str, Any] | None:
    """Strongly-consistent GetItem on ``IDEM#{principal}#{idempotency_key}``."""
    resp = _jobs_table().get_item(
        Key={"pk": f"IDEM#{principal}#{idempotency_key}"}, ConsistentRead=True
    )
    return cast("dict[str, Any] | None", resp.get("Item"))


def get_job(job_id: str) -> dict[str, Any] | None:
    """Strongly-consistent GetItem on ``JOB#{job_id}`` -- default DynamoDB read
    behaviour for a single-table GetItem, no ``ConsistentRead`` needed to opt
    in beyond the default (which is already strongly consistent unless
    eventual consistency is explicitly requested)."""
    resp = _jobs_table().get_item(Key={"pk": f"JOB#{job_id}"})
    return cast("dict[str, Any] | None", resp.get("Item"))


@dataclass(frozen=True)
class NewJob:
    """Everything :func:`submit_job` needs to write one ``JOB#`` item.

    ``canonical_request`` and ``provenance`` are plain, already-JSON-safe
    dicts (Section 6.2a: the request lives IN the job item, never in S3).
    """

    job_id: str
    canonical_request: dict[str, Any]
    provenance: dict[str, Any]


def submit_job(
    new_job: NewJob,
    *,
    principal: str | None,
    idempotency_key: str | None,
    client_request_hash: str | None,
    assigned_seed: int | None,
    resolved_model_version: str | None,
    resolved_artifact_id: str | None,
    idem_ttl_seconds: int = 24 * 3600,
) -> None:
    """Persist a new job, atomically with its idempotency record when one applies.

    Mirrors Section 6.2's ``TransactWriteItems`` exactly: with an
    ``idempotency_key``, both the ``IDEM#`` and ``JOB#`` items are written in
    one transaction, each conditioned on ``attribute_not_exists(pk)``;
    without one, a single conditional ``PutItem`` of ``JOB#`` only (Section
    6.2, "the request is treated as unique").

    Raises :class:`IdempotencyConflict` when the ``IDEM#`` condition lost a
    race, and :class:`JobIdCollision` when the ``JOB#`` condition did (a
    UUIDv4 collision) -- the caller distinguishes the two by which one
    applies to its call (an idempotency key was or was not supplied).
    """
    table_name = os.environ["SCENARIO_JOBS_TABLE"]
    job_item = {
        "pk": f"JOB#{new_job.job_id}",
        "sk": "META",
        "job_id": new_job.job_id,
        "status": "SUBMITTED",
        # DynamoDB's number type rejects a raw Python `float` outright (both
        # via this module's own low-level TransactWriteItems serialization
        # below AND via the high-level Table resource's plain put_item --
        # neither accepts one): risk_levels (e.g. [0.95, 0.99]) must be
        # Decimal before either write path touches it.
        "request": to_decimal(new_job.canonical_request),
        "provenance": new_job.provenance,
        # execution_arn deliberately ABSENT until StartExecution succeeds (6.2a).
    }

    if idempotency_key is None:
        try:
            _jobs_table().put_item(
                Item=job_item, ConditionExpression="attribute_not_exists(pk)"
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise JobIdCollision(new_job.job_id) from exc
            raise
        return

    if principal is None or client_request_hash is None:
        raise ValueError(
            "principal and client_request_hash are required with an idempotency key"
        )

    idem_item = {
        "pk": f"IDEM#{principal}#{idempotency_key}",
        "sk": "META",
        "job_id": new_job.job_id,
        "client_request_hash": client_request_hash,
        "assigned_seed": assigned_seed,
        "resolved_model_version": resolved_model_version,
        "resolved_artifact_id": resolved_artifact_id,
        "created_at": _now_iso(),
        "ttl": int(time.time()) + idem_ttl_seconds,
    }

    try:
        _client().transact_write_items(
            TransactItems=[
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": _to_dynamo_item(idem_item),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
                {
                    "Put": {
                        "TableName": table_name,
                        "Item": _to_dynamo_item(job_item),
                        "ConditionExpression": "attribute_not_exists(pk)",
                    }
                },
            ]
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] != "TransactionCanceledException":
            raise
        reasons = exc.response.get("CancellationReasons", [])
        idem_failed = bool(reasons) and reasons[0].get("Code") == "ConditionalCheckFailed"
        job_failed = len(reasons) > 1 and reasons[1].get("Code") == "ConditionalCheckFailed"
        if idem_failed:
            raise IdempotencyConflict(idempotency_key) from exc
        if job_failed:
            raise JobIdCollision(new_job.job_id) from exc
        raise


def set_execution_arn(job_id: str, execution_arn: str) -> bool:
    """Conditional UpdateItem: ``execution_arn`` absent -> present, ``status`` -> QUEUED.

    Section 6.2a's healing invariant. Returns ``True`` if this call performed
    the update, ``False`` if the condition failed (someone else already set
    it -- the caller should treat that as success too, per the healing
    table's "status == SUBMITTED and execution_arn present -> return 200,
    nothing to do").
    """
    try:
        _jobs_table().update_item(
            Key={"pk": f"JOB#{job_id}"},
            UpdateExpression="SET execution_arn = :arn, #status = :queued",
            ConditionExpression="attribute_not_exists(execution_arn)",
            ExpressionAttributeNames={"#status": "status"},
            ExpressionAttributeValues={":arn": execution_arn, ":queued": "QUEUED"},
        )
        return True
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise


def mark_cancelled(job_id: str) -> None:
    """``DELETE /scenario-jobs/{job_id}`` -> ``status = CANCELLED`` (Section 6.1 step 7)."""
    _jobs_table().update_item(
        Key={"pk": f"JOB#{job_id}"},
        UpdateExpression="SET #status = :cancelled",
        ExpressionAttributeNames={"#status": "status"},
        ExpressionAttributeValues={":cancelled": "CANCELLED"},
    )


# --- model-registry table: CANDIDATE# / APPROVAL# / POINTER# ---------------


def get_candidate(family: str, model_version: str) -> dict[str, Any] | None:
    """GetItem ``pk=CANDIDATE#{family}#{model_version}``, ``sk=META``.

    Section 13.1 row 4."""
    resp = _registry_table().get_item(
        Key={"pk": f"CANDIDATE#{family}#{model_version}", "sk": "META"}
    )
    return cast("dict[str, Any] | None", resp.get("Item"))


def get_approval(family: str, model_version: str) -> dict[str, Any] | None:
    """GetItem ``pk=APPROVAL#{family}#{model_version}``, ``sk=META``.

    Section 13.1 row 5."""
    resp = _registry_table().get_item(
        Key={"pk": f"APPROVAL#{family}#{model_version}", "sk": "META"}
    )
    return cast("dict[str, Any] | None", resp.get("Item"))


def get_pointer(family: str) -> dict[str, Any] | None:
    """GetItem ``pk=POINTER#{family}``, ``sk=CURRENT`` (Section 13.1 row 6)."""
    resp = _registry_table().get_item(Key={"pk": f"POINTER#{family}", "sk": "CURRENT"})
    return cast("dict[str, Any] | None", resp.get("Item"))


def to_decimal(value: Any) -> Any:
    """Recursively convert plain ``float`` to ``decimal.Decimal`` via its
    string representation (never ``Decimal(float)`` directly, which would
    bake in binary floating-point representation error, e.g.
    ``Decimal(0.95)`` != ``Decimal('0.95')``) -- the write-side counterpart
    of :func:`to_native` below. DynamoDB's ``TypeSerializer`` rejects a raw
    Python ``float`` outright (``TypeError: Float types are not supported.
    Use Decimal types instead.``), for both this module's own low-level
    ``transact_write_items`` calls and the high-level ``Table`` resource's
    ordinary ``put_item``.
    """
    from decimal import Decimal

    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, list):
        return [to_decimal(item) for item in value]
    if isinstance(value, dict):
        return {key: to_decimal(item) for key, item in value.items()}
    return value


def to_native(value: Any) -> Any:
    """Recursively convert ``decimal.Decimal`` (boto3's DynamoDB number type)
    to plain ``int``/``float``.

    The high-level ``Table`` resource returns every DynamoDB number
    attribute as ``Decimal``, never ``int``/``float`` -- correct for
    DynamoDB's own arbitrary-precision number type, but not what
    ``worker/__main__.py``'s strict request parser expects (it checks
    ``isinstance(value, int)`` deliberately, to reject a fractional
    ``horizon`` rather than silently truncating one; a bare ``Decimal``
    would fail that check even for a genuinely whole number). Used by the
    worker's ``--job-id`` path on the job document read back from
    DynamoDB, never by ``submit.py`` writing it (which writes plain
    Python ``int``/``bool``/``str``/``None`` in the first place).
    """
    from decimal import Decimal

    if isinstance(value, Decimal):
        as_int = int(value)
        return as_int if as_int == value else float(value)
    if isinstance(value, list):
        return [to_native(item) for item in value]
    if isinstance(value, dict):
        return {key: to_native(item) for key, item in value.items()}
    return value


def _now_iso() -> str:
    from datetime import UTC, datetime

    return datetime.now(UTC).isoformat()


def _to_dynamo_item(item: dict[str, Any]) -> dict[str, Any]:
    """Strip ``None`` values (DynamoDB rejects them for most types) before a
    raw ``transact_write_items`` call -- the high-level ``Table`` resource
    does this automatically for ``put_item``/``update_item``, but
    ``client.transact_write_items`` takes low-level attribute-value dicts, so
    this module serializes through ``boto3.dynamodb.types.TypeSerializer``
    and applies the same ``None``-stripping convention by hand.
    """
    from boto3.dynamodb.types import TypeSerializer

    serializer = TypeSerializer()
    return {k: serializer.serialize(v) for k, v in item.items() if v is not None}
