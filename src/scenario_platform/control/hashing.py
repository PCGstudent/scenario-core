"""The idempotency hash (architecture plan Section 6.2).

Computed over the CALLER-PROVIDED semantic request only -- excluding every
server-generated value (``assigned_seed``, resolved ``model_version``,
resolved ``artifact_id``, ``job_id``, timestamps) so that a retry hashes to
the same value as the original call regardless of what the platform decided
on the caller's behalf. ``seed`` is included ONLY if the caller supplied one
-- a request with no ``seed`` and a request with ``seed: null`` are
deliberately different well-formed inputs here (``ScenarioJobIn.seed`` being
``None`` collapses that distinction at the pydantic layer, which is fine:
JSON has no way to send "the key is present but means absent" separately
from "the key is absent", so the two are treated identically -- both exclude
``seed`` from the hash).

This is a distinct scheme from ``scenario_platform.domain.identity``'s
canonical artifact/dataset encoding: that module hashes *decoded semantic
content* (arrays, floats) with a custom binary layout for byte-exact,
storage-format-independent identity (AGENTS.md invariant 29). This one
hashes a small, JSON-shaped API request for de-duplication -- ordinary
canonical JSON (sorted keys, no whitespace) is precise enough for that
purpose and does not need a custom binary scheme.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .schemas import ScenarioJobIn

#: Field order is irrelevant to the hash value (canonical JSON sorts keys),
#: but pinned here anyway as the single documented list of what is IN scope
#: -- anything on ScenarioJobIn not in this tuple must never silently start
#: being hashed (or stop being hashed) without this list changing too.
_HASHED_FIELDS: tuple[str, ...] = (
    "model_version",
    "horizon",
    "n_paths",
    "initial_state",
    "metrics",
    "rng_scheme",
    "include_variance",
    "governance",
)


def _canonical_json(value: dict[str, Any]) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("ascii")


def client_request_hash(request: ScenarioJobIn) -> str:
    """``sha256(canonical_json({...caller-provided semantic fields...}))``."""
    payload: dict[str, Any] = {}
    for name in _HASHED_FIELDS:
        value = getattr(request, name)
        if name == "initial_state" and isinstance(value, tuple):
            value = list(value)
        if name == "governance" and value is not None:
            value = value.model_dump()
        payload[name] = value
    if request.seed is not None:
        payload["seed"] = request.seed
    digest = hashlib.sha256(_canonical_json(payload)).hexdigest()
    return f"sha256:{digest}"
