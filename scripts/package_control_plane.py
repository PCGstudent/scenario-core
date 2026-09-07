"""Build the control-plane Lambda deployment zip.

Bundles ``scenario_platform.{control,domain,adapters}`` plus
``requirements/control.lock``'s third-party packages (installed for Lambda's
own platform/Python version via ``pip install --platform manylinux2014_x86_64
--python-version 3.13 --only-binary=:all: --target``) into one zip, referenced
by ``infra/terraform/modules/{job_api,job_orchestrator}``'s
``lambda_package_path`` variable.

``scenario_platform.domain`` is included because ``scenario_platform.control.
admission`` imports ``scenario_platform.domain.policies`` directly (Section
7.4's "shared contract without shared runtime" -- the domain layer is pure
Python with no scientific dependency, so bundling it costs nothing and keeps
policy logic defined exactly once). ``scenario_platform.worker`` is
deliberately NOT bundled: nothing in the control plane imports it, and it
would pull in a scientific-stack import surface this package has no
business carrying (the same boundary
``tests/test_control_plane_purity.py`` checks against the lock file, checked
here again against the actual zip contents).

Run in CI (``deploy-dev.yml``, extended) on a Linux runner matching the
Lambda execution environment -- ``pip install --platform`` can download
manylinux wheels from any host, but this script does not attempt to
cross-compile anything with a native extension of its own (none of
control.lock's packages need one: pydantic-core ships as a prebuilt wheel).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_PACKAGES = ("control", "domain", "adapters")


def build(*, output: Path, python_version: str = "3.13") -> None:
    build_dir = REPO_ROOT / "build" / "control-plane-staging"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--no-deps",
            "--platform",
            "manylinux2014_x86_64",
            "--python-version",
            python_version,
            "--only-binary=:all:",
            "--target",
            str(build_dir),
            "-r",
            str(REPO_ROOT / "requirements" / "control.lock"),
        ],
        check=True,
    )

    package_root = build_dir / "scenario_platform"
    package_root.mkdir(exist_ok=True)
    (package_root / "__init__.py").write_text("", encoding="utf-8")
    for name in _BUNDLED_PACKAGES:
        shutil.copytree(
            REPO_ROOT / "src" / "scenario_platform" / name,
            package_root / name,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(build_dir.rglob("*")):
            if path.is_file():
                zf.write(path, path.relative_to(build_dir))

    print(f"Wrote {output} ({output.stat().st_size / 1_000_000:.1f} MB)")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "build" / "control-plane.zip"
    )
    parser.add_argument("--python-version", default="3.13")
    args = parser.parse_args(argv)
    build(output=args.output, python_version=args.python_version)
    return 0


if __name__ == "__main__":
    sys.exit(main())
