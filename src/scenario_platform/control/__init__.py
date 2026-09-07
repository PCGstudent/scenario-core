"""Phase 3b control plane: thin Lambda handlers over the Phase 1 domain layer.

Architecture plan Section 24, Phase 3b's Files bullet. No numpy, no scipy,
no scientific stack (``tests/test_control_plane_purity.py`` on
``requirements/control.lock``) -- only ``pydantic`` and ``boto3``. boto3 is
permitted here (unlike in ``scenario_platform.domain``/``worker``) because
this is a different plane; see ``pyproject.toml``'s per-file Ruff exception.
"""

from __future__ import annotations
