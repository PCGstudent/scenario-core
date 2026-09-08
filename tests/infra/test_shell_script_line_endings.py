"""``scripts/run_container_acceptance.sh`` and ``run.sh`` are meant to run on
both a Windows checkout (Git-Bash/MSYS) and a genuine Linux host (WSL, CI's
own self-hosted runner) -- but without ``.gitattributes`` forcing LF, a
Windows clone with ``core.autocrlf=true`` checks these files out with CRLF
line endings, which native Linux bash rejects outright
(``$'\r': command not found``, ``set: pipefail: invalid option name``).
Reproduced directly against this exact repository: ``run_container_acceptance.sh``
failed at line 24/25 under WSL Ubuntu before ``.gitattributes`` was added.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _tracked_shell_scripts() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "*.sh"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in result.stdout.splitlines() if line]


def test_gitattributes_forces_lf_for_shell_scripts():
    gitattributes = (REPO_ROOT / ".gitattributes").read_text(encoding="utf-8")
    assert "*.sh text eol=lf" in gitattributes


def test_tracked_shell_scripts_have_no_crlf_in_git_history():
    """Checks the committed blob content (via ``git show HEAD:<path>``), not
    the working-tree file -- the latter is exactly what a Windows checkout's
    own autocrlf conversion would silently "fix" back to CRLF locally,
    making a plain filesystem read of this test worthless as a regression
    guard."""
    scripts = _tracked_shell_scripts()
    assert scripts, "expected at least one tracked .sh file"
    for path in scripts:
        blob = subprocess.run(
            ["git", "show", f"HEAD:{path}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        assert b"\r\n" not in blob, f"{path} is committed with CRLF line endings"
