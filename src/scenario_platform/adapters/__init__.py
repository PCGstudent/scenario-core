"""Boundary adapters: where a real transport/observability sink would attach.

Phase 2 provides local/stdout implementations only (``logging.py``,
``metrics.py``) -- no ``boto3``, no AWS concept, no infrastructure
environment configuration (AGENTS.md invariant 28, same as ``domain/``).
Phase 3 adds ``s3_store.py`` and ``job_store.py`` here; this package is
where that AWS coupling is allowed to live, and nowhere else in
``scenario_platform``.
"""

from __future__ import annotations
