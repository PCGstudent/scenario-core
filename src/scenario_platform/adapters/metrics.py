"""The worker's metrics boundary (architecture plan Section 22: ``adapters/metrics.py``,
"EMF emitter").

Embedded Metric Format (EMF) is a plain JSON envelope convention: a single
JSON object per line, written to stdout, that CloudWatch's log agent scrapes
metrics out of directly -- there is no `PutMetricData` API call, no AWS SDK,
and no credential anywhere in this module. That is what makes it possible to
"expose the documented metrics boundary without embedding AWS-specific
infrastructure" (Phase 2's own requirement): this module is the *complete*
metrics boundary, in Phase 2 and in Phase 3 alike, and Phase 3 changing
nothing here to start actually reaching CloudWatch is the proof that the
boundary was drawn in the right place.

**Cardinality discipline (deliberate, not an oversight).** EMF dimensions
multiply CloudWatch's stored metric cardinality and cost, so this module's
public API only accepts a fixed, small dimension set --
:data:`ALLOWED_DIMENSION_KEYS` -- and raises rather than silently accepting
a caller's attempt to pass ``job_id``, ``artifact_id``, ``seed`` or any
other high-cardinality value as a *dimension*. Those identifiers are exactly
the kind of values worth keeping for correlation, so they may still be
attached as plain (non-dimension) JSON properties on the same record --
which is where the call sites in ``worker/__main__.py`` put them.
"""

from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TextIO

NAMESPACE = "ScenarioCore/Worker"

#: The only metric dimension keys this module will emit. Both are small,
#: fixed-cardinality vocabularies (operation names; the four ExitCode/
#: error_class values) -- never an identifier that varies per request.
ALLOWED_DIMENSION_KEYS = frozenset({"Operation", "ErrorClass"})


def emit_metric(
    *,
    name: str,
    value: float,
    unit: str,
    dimensions: dict[str, str],
    properties: dict[str, object] | None = None,
    stream: TextIO = sys.stdout,
) -> None:
    """Write one EMF-shaped JSON line reporting a single metric.

    ``dimensions`` becomes the EMF ``Dimensions`` set (must be a subset of
    :data:`ALLOWED_DIMENSION_KEYS`); ``properties`` are additional plain
    fields on the same JSON object for correlation (e.g. ``artifact_id``,
    ``model_version``) that are deliberately *not* declared as dimensions.
    """
    unknown = set(dimensions) - ALLOWED_DIMENSION_KEYS
    if unknown:
        raise ValueError(
            f"refusing to emit {unknown} as EMF dimensions -- only "
            f"{sorted(ALLOWED_DIMENSION_KEYS)} are permitted, to keep CloudWatch "
            "metric cardinality bounded; pass high-cardinality identifiers via "
            "`properties` instead"
        )

    now_ms = int(datetime.now(tz=UTC).timestamp() * 1000)
    payload: dict[str, object] = {
        "_aws": {
            "Timestamp": now_ms,
            "CloudWatchMetrics": [
                {
                    "Namespace": NAMESPACE,
                    "Dimensions": [list(dimensions.keys())] if dimensions else [[]],
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        name: value,
        **dimensions,
        **(properties or {}),
    }
    stream.write(json.dumps(payload, default=str, sort_keys=True) + "\n")
    stream.flush()


def emit_duration_ms(
    *, operation: str, duration_ms: float, error_class: str | None, **properties: object
) -> None:
    """Convenience wrapper: the one metric every worker invocation reports."""
    emit_metric(
        name="DurationMs",
        value=duration_ms,
        unit="Milliseconds",
        dimensions={"Operation": operation, "ErrorClass": error_class or "NONE"},
        properties=properties,
    )
