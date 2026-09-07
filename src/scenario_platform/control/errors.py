"""HTTP response shaping for API Gateway HTTP API payload format 2.0.

Every control-plane Lambda returns through :func:`json_response` so the
proxy-integration response shape (``statusCode``/``headers``/``body``) is
defined exactly once, and every rejection carries a ``HandlerError`` with an
explicit status code rather than an uncaught exception turning into API
Gateway's own generic 500.
"""

from __future__ import annotations

import json
from typing import Any


class HandlerError(Exception):
    """A control-plane rejection with a known HTTP status code."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


def json_response(status_code: int, body: dict[str, Any]) -> dict[str, Any]:
    return {
        "statusCode": status_code,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, default=str),
    }


def error_response(exc: HandlerError) -> dict[str, Any]:
    return json_response(exc.status_code, {"error": exc.error, "message": exc.message})


def principal_arn(event: dict[str, Any]) -> str:
    """The calling principal's ARN from API Gateway's AWS_IAM authorizer context.

    Primary enforcement of authentication is API Gateway's own
    ``authorization_type = "AWS_IAM"`` on every route (Terraform, verified
    only by a post-deploy smoke test -- an unauthenticated request never
    reaches this Lambda at all). This check is defense in depth: a request
    that somehow arrives without that context is rejected here too, rather
    than trusted.
    """
    try:
        return str(event["requestContext"]["authorizer"]["iam"]["userArn"])
    except (KeyError, TypeError) as exc:
        raise HandlerError(401, "Unauthorized", "missing IAM authorizer context") from exc
