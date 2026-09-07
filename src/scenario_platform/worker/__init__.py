"""The production worker: a deterministic CLI boundary over the Phase-1 domain services.

No AWS import anywhere in this package (AGENTS.md invariant 28) -- job
input/output is local files today (``--request``/``--artifact-dir``/
``--output-dir``); Phase 3 replaces those with DynamoDB/S3 reads through
``scenario_platform.adapters``, not by adding AWS calls here.
"""

from __future__ import annotations
