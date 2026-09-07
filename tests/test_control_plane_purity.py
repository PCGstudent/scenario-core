"""Guards that requirements/control.lock never gains a scientific dependency.

The control plane is a thin Lambda layer (architecture plan Section 11.1):
request validation, admission control, job identity, metadata reads and
writes. It must never carry the ~0.5GB scientific closure the quantitative
worker needs, both because a cold start has no reason to pay for it and
because its presence would be a sign the control/data-plane boundary has
eroded (architecture plan Section 4.1). This test checks the resolved lock,
not just the hand-written ``requirements/control.in``, so a dependency
pulled in transitively is caught exactly the same way a direct one would be.

Package names are compared after PEP 503 normalization (case-folded, with
``.``/``_``/``-`` runs collapsed to a single ``-``) so that this is genuinely
name-aware rather than a substring match: a hypothetical package named
``pandas-stubs`` or ``numpydoc`` must not trip a check that is only supposed
to catch ``pandas`` and ``numpy`` themselves.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTROL_LOCK = REPO_ROOT / "requirements" / "control.lock"

# The scientific/UI stack that has no place in the control plane (architecture
# plan Section 7.3's import-boundary table).
_FORBIDDEN = frozenset(
    {"numpy", "scipy", "pandas", "statsmodels", "arch", "matplotlib", "streamlit", "plotly"}
)

_REQUIREMENT_LINE = re.compile(
    r"""
    ^
    (?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)   # PEP 508 project name
    \s*==\s*
    [A-Za-z0-9][A-Za-z0-9._+!-]*           # pinned version, not captured
    """,
    re.VERBOSE,
)


def _normalize(name: str) -> str:
    """PEP 503 normalization: case-fold and collapse -._ runs to a single '-'."""
    return re.sub(r"[-_.]+", "-", name).lower()


def _locked_package_names(lock_path: Path) -> set[str]:
    names: set[str] = set()
    for raw_line in lock_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--hash"):
            continue
        match = _REQUIREMENT_LINE.match(line)
        if match:
            names.add(_normalize(match.group("name")))
    return names


def test_control_lock_exists():
    assert CONTROL_LOCK.is_file(), (
        "requirements/control.lock is missing; regenerate it with "
        "'uv pip compile requirements/control.in --universal "
        "--python-version 3.13 --generate-hashes -o requirements/control.lock'"
    )


def test_control_lock_has_no_scientific_stack():
    locked = _locked_package_names(CONTROL_LOCK)
    forbidden_present = {_normalize(name) for name in _FORBIDDEN} & locked
    assert not forbidden_present, (
        "requirements/control.lock pulled in a scientific/UI dependency the thin "
        f"control plane must never carry: {sorted(forbidden_present)}"
    )


def test_control_lock_is_not_accidentally_empty():
    """A lock with zero packages would pass the check above for the wrong reason."""
    assert _locked_package_names(CONTROL_LOCK), "control.lock resolved to no packages"


def test_parser_actually_detects_a_forbidden_package():
    """Negative control: the name-matching logic must be able to fail.

    Exercises both the direct hit and the normalization it claims to do
    (mixed case, underscore instead of hyphen) without depending on what
    happens to be in the real lock file today.
    """
    sample = "\n".join(
        [
            "# header comment, must be ignored",
            "pydantic==2.13.5 \\",
            "    --hash=sha256:deadbeef",
            "NumPy==2.2.6 \\",
            "    --hash=sha256:deadbeef",
        ]
    )
    names = set()
    for raw_line in sample.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or line.startswith("--hash"):
            continue
        match = _REQUIREMENT_LINE.match(line)
        if match:
            names.add(_normalize(match.group("name")))

    assert names == {"pydantic", "numpy"}
    assert {_normalize(n) for n in _FORBIDDEN} & names == {"numpy"}


def test_normalization_does_not_false_positive_on_a_lookalike_name():
    """A package that merely CONTAINS a forbidden name must not be flagged."""
    assert _normalize("numpydoc") not in {_normalize(name) for name in _FORBIDDEN}
    assert _normalize("pandas-stubs") not in {_normalize(name) for name in _FORBIDDEN}
