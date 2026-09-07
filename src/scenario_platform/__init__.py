"""Production scenario-generation platform layer.

Phase 0 reserved this package (empty, so the mypy/Ruff configuration in
pyproject.toml and the AWS-boundary guard test had a real target from the
start). Phase 1 adds ``domain``: ModelArtifact, canonical identity,
request/response schemas, and the fit/simulate/validate/risk services --
see docs/architecture/IMPLEMENTATION_PLAN.md Sections 7-8. Later phases add
adapters, the worker CLI and the control-plane handlers. No AWS import
anywhere under this package (AGENTS.md invariant 28).
"""
