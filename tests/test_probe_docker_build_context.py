"""``docker/probe.Dockerfile`` needs ``scripts/connectivity_probe.py`` in its
build context, but the repo-root ``.dockerignore`` excludes all of
``scripts/`` -- written for ``docker/worker.Dockerfile`` only, which never
needs that directory. ``.dockerignore`` applies to the whole build context
regardless of ``-f``, so this blocked probe.Dockerfile's own COPY step,
confirmed directly: ``docker build -f docker/probe.Dockerfile .`` failed
with ``"/scripts/connectivity_probe.py": not found`` before this fix.

Fixed via a sibling ``docker/probe.Dockerfile.dockerignore`` (Docker picks
this up automatically for a matching ``-f``) that excludes everything and
re-includes only what this one Dockerfile needs -- never by loosening the
worker's own ignore file, which stays exactly as restrictive as before.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent
PROBE_IGNORE = REPO_ROOT / "docker" / "probe.Dockerfile.dockerignore"
WORKER_IGNORE = REPO_ROOT / ".dockerignore"
PROBE_DOCKERFILE = REPO_ROOT / "docker" / "probe.Dockerfile"


def _lines(path: Path) -> list[str]:
    return [
        line.strip()
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]


def test_probe_dockerignore_exists():
    assert PROBE_IGNORE.is_file()


def test_probe_dockerignore_excludes_everything_by_default():
    lines = _lines(PROBE_IGNORE)
    assert lines[0] == "**", "the allowlist must start by excluding the entire context"


def test_probe_dockerignore_reincludes_exactly_the_two_needed_paths_and_parents():
    lines = set(_lines(PROBE_IGNORE))
    required = {
        "**",
        "!docker/",
        "!docker/probe.Dockerfile",
        "!scripts/",
        "!scripts/connectivity_probe.py",
    }
    assert lines == required, f"unexpected extra or missing patterns: {lines ^ required}"


def test_probe_dockerfile_still_copies_the_connectivity_probe_script():
    text = PROBE_DOCKERFILE.read_text(encoding="utf-8")
    assert "COPY scripts/connectivity_probe.py" in text


def test_worker_dockerignore_still_excludes_probe_only_paths():
    """The worker's own ignore file must remain exactly as restrictive as
    before this fix -- the probe's needs are met entirely by the sibling
    allowlist file, never by loosening this one."""
    lines = _lines(WORKER_IGNORE)
    for must_still_exclude in ("scripts/", "docker/", "tests/", "docs/", "infra/"):
        assert must_still_exclude in lines, f"{must_still_exclude!r} must remain excluded"
