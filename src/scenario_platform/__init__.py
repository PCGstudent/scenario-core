"""Production scenario-generation platform layer.

Empty in Phase 0 by design: this package is reserved so that the mypy and
Ruff configuration in pyproject.toml (and the AWS-boundary guard test,
tests/test_no_aws_in_core.py) have a real target from the start, per
docs/architecture/IMPLEMENTATION_PLAN.md Phase 0. Phase 1 adds the domain
layer (ModelArtifact, canonical identity, request/response schemas); later
phases add adapters, the worker CLI and the control-plane handlers. Nothing
here executes anything.
"""
