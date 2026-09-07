"""Structured JSON logging for the worker (architecture plan Section 22's
repository tree: ``adapters/logging.py``, "JSON formatter with contextual
job_id").

A "local/stdout implementation" (Phase 2's Files bullet) rather than an AWS
one: this module writes one JSON object per line to a stream (stdout by
default) using nothing but the standard library ``logging`` module. That is
deliberate, not a stopgap -- CloudWatch Logs (via the Fargate awslogs driver,
Phase 3) ingests exactly this: whatever the container writes to stdout/stderr,
line by line. There is no AWS SDK call anywhere in this module, so nothing
here needs credentials, and nothing here needs to change when Phase 3 starts
shipping the same lines to CloudWatch.

Field discipline (explicit, because getting it wrong here means leaking data
into a system with a long retention window): every field is either a small
scalar (a string, number, or bool) or omitted entirely -- never a full
returns array, never a full request/artifact payload, never a secret. The
worker's own call sites are responsible for only ever passing identifiers
and summary values through ``extra``; this formatter does not attempt to
truncate or redact anything itself, because a formatter that silently
truncates can hide the fact that a call site was about to log something it
should not have.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any, TextIO

#: Standard `logging.LogRecord` attributes -- anything NOT in this set that
#: appears on a record is a caller-supplied contextual field (job_id,
#: artifact_id, operation, ...) and is folded into the JSON output
#: alongside the standard ones. This is what makes `logger.info(...,
#: extra={"artifact_id": ...})` show up as a top-level JSON field rather
#: than being silently dropped.
_STANDARD_RECORD_ATTRS = frozenset(logging.LogRecord("", 0, "", 0, "", (), None).__dict__)


class JsonFormatter(logging.Formatter):
    """One JSON object per line: ``timestamp``, ``level``, ``logger``, ``message``,
    plus any contextual fields passed via ``extra=``.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _STANDARD_RECORD_ATTRS and key not in payload:
                payload[key] = value
        if record.exc_info is not None:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str, sort_keys=True)


def configure_worker_logging(
    *, level: int = logging.INFO, stream: TextIO = sys.stdout
) -> logging.Logger:
    """Configure and return the ``scenario_platform.worker`` logger.

    Idempotent: safe to call more than once (e.g. once from ``__main__`` and
    once from a test) -- clears any handlers this call previously attached
    rather than accumulating duplicate log lines.
    """
    logger = logging.getLogger("scenario_platform.worker")
    logger.handlers.clear()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger
