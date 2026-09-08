"""Build the control-plane Lambda deployment zip.

Bundles ``scenario_platform.{control,domain,adapters}`` plus a **filtered**
subset of ``requirements/control.lock``'s third-party packages -- pydantic
and its own dependency closure only -- installed for Lambda's platform/Python
version via ``pip install --platform manylinux2014_x86_64 --python-version
3.13 --only-binary=:all: --target``. Referenced by
``infra/terraform/modules/{job_api,job_orchestrator}``'s
``lambda_package_path`` variable.

**boto3/botocore are deliberately excluded from this zip**, even though they
are real, pinned entries in ``requirements/control.lock`` (needed there for
local dev/testing reproducibility, including the ``moto``-backed test suite).
The AWS Lambda Python 3.13 managed runtime supplies boto3/botocore; this
is an explicit unpinned runtime dependency and a deployment trade-off.
Botocore's own source alone is ~16 MB unpacked, which by itself
blows the Phase 3b package-size acceptance criterion (< 15 MB, Section 24).
A first attempt at this script bundled the whole lock file unfiltered and
produced a 21.3 MB zip; excluding the boto3 family (boto3, botocore,
s3transfer, jmespath, python-dateutil, six, urllib3 -- every package whose
lock-file "# via" comment traces back to boto3, none of which pydantic
itself needs) is what brings it back under budget. If a specific boto3/
botocore version pin is ever genuinely required at runtime (not just for
local testing), that is a deliberate, reviewed addition to this exclusion
list, not something to silently reintroduce by reverting to the whole lock
file.

Only ``domain/__init__.py`` and ``domain/policies.py`` are included because
``scenario_platform.control.admission`` imports the governance policy. This
keeps policy defined exactly once while making it structurally impossible for
the ZIP to contain the scientific artifact/serialization/services runtime.
``scenario_platform.worker`` as a whole
is deliberately NOT bundled (it would pull in ``__main__.py``'s full
scientific-stack import surface) -- only ``worker/__init__.py`` and
``worker/errors.py`` are, because ``control.classify_failure`` imports the
latter's ``ERROR_CLASS_BY_EXIT_CODE`` (the exit-code/error_class mapping
defined once and shared, Section 6.3/8.3), and both files are pure stdlib.

Run in CI (``deploy-dev.yml``, extended) on a Linux runner matching the
Lambda execution environment -- ``pip install --platform`` can download
manylinux wheels from any host, but this script does not attempt to
cross-compile anything with a native extension of its own (pydantic-core
ships as a prebuilt wheel).
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_BUNDLED_PACKAGES = ("control", "adapters")
_DOMAIN_FILES_NEEDED = ("__init__.py", "policies.py")

#: scenario_platform.control.classify_failure imports
#: scenario_platform.worker.errors (the shared exit-code/error_class
#: enumeration, Section 6.3/8.3 -- defined exactly once, reused by both the
#: worker and the classifier rather than duplicated). Bundling the whole
#: `worker` package would pull in `__main__.py`'s scientific-stack imports;
#: `errors.py` itself is pure stdlib (IntEnum + a dict), so only these two
#: files are copied -- proven necessary and sufficient by the Docker-based
#: clean-room import check this script's own acceptance step performs.
_WORKER_FILES_NEEDED = ("__init__.py", "errors.py")

#: Every package in requirements/control.lock whose own "# via" comment
#: traces back to boto3 -- excluded from the deployment zip because the
#: Lambda runtime already provides boto3/botocore (see module docstring).
#: Matched case-insensitively against the lock file's own package names.
_EXCLUDED_FROM_ZIP = frozenset(
    {"boto3", "botocore", "s3transfer", "jmespath", "python-dateutil", "six", "urllib3"}
)

_REQUIREMENT_LINE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9._-]*)==([A-Za-z0-9.\-+!]+)")


def _filtered_requirements(lock_path: Path) -> list[str]:
    """Keep complete locked requirement blocks, including all wheel hashes."""
    blocks: list[str] = []
    current: list[str] = []
    keep = False
    for line in lock_path.read_text(encoding="utf-8").splitlines():
        match = _REQUIREMENT_LINE.match(line)
        if match:
            if current and keep:
                blocks.append("\n".join(current))
            current = [line]
            keep = match.group(1).lower() not in _EXCLUDED_FROM_ZIP
        elif current and line.lstrip().startswith("--hash="):
            current.append(line)
    if current and keep:
        blocks.append("\n".join(current))
    return blocks


def build(*, output: Path, python_version: str = "3.13", skip_verify: bool = False) -> None:
    build_dir = REPO_ROOT / "build" / "control-plane-staging"
    if build_dir.exists():
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True)

    pins = _filtered_requirements(REPO_ROOT / "requirements" / "control.lock")
    if not pins:
        raise RuntimeError("no packages survived filtering -- check control.lock parsing")

    filtered_lock = build_dir / "control-filtered.lock"
    filtered_lock.write_text("\n".join(pins) + "\n", encoding="utf-8")
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
            "--require-hashes",
            "-r",
            str(filtered_lock),
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

    domain_root = package_root / "domain"
    domain_root.mkdir(exist_ok=True)
    for filename in _DOMAIN_FILES_NEEDED:
        shutil.copy2(
            REPO_ROOT / "src" / "scenario_platform" / "domain" / filename,
            domain_root / filename,
        )

    worker_root = package_root / "worker"
    worker_root.mkdir(exist_ok=True)
    for filename in _WORKER_FILES_NEEDED:
        shutil.copy2(
            REPO_ROOT / "src" / "scenario_platform" / "worker" / filename,
            worker_root / filename,
        )

    output.parent.mkdir(parents=True, exist_ok=True)
    if output.exists():
        output.unlink()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(build_dir.rglob("*")):
            if path.is_file() and "__pycache__" not in path.parts:
                zf.write(path, path.relative_to(build_dir))

    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
    forbidden_domain_files = {
        f"scenario_platform/domain/{name}"
        for name in ("artifacts.py", "identity.py", "serialization.py", "services.py")
    }
    leaked_files = sorted(names & forbidden_domain_files)
    if leaked_files:
        raise RuntimeError(
            f"scientific domain files leaked into Lambda ZIP: {leaked_files}"
        )

    size_mb = output.stat().st_size / 1_000_000
    print(f"Wrote {output} ({size_mb:.1f} MB)")
    if size_mb >= 15:
        raise RuntimeError(
            f"{output} is {size_mb:.1f} MB, at or over the 15 MB Phase 3b "
            "acceptance ceiling (architecture plan Section 24)"
        )

    if skip_verify:
        print("Skipped --skip-verify: NOT proven importable in a clean Lambda runtime.")
    else:
        import tempfile

        with tempfile.TemporaryDirectory(prefix="lambda-zip-verify-") as extracted:
            with zipfile.ZipFile(output) as archive:
                archive.extractall(extracted)
            verify(Path(extracted))


#: Every module a scientific-purity check must never see imported after
#: loading the three handlers below -- the same list
#: tests/test_control_plane_purity.py checks against the lock file, checked
#: here again against what the zip's content actually imports at runtime,
#: which the lock-file check alone cannot see (a package can be excluded
#: from the lock and still get pulled in by a stray same-plane import, as
#: `classify_failure`'s original `scenario_platform.worker.errors` import
#: proved in practice before `worker/__main__.py` was excluded from it).
_FORBIDDEN_AT_RUNTIME = ("numpy", "scipy", "pandas", "statsmodels", "arch", "matplotlib")

_VERIFY_SCRIPT = f"""
import sys
sys.path.insert(0, "/var/task")
import scenario_platform.control.submit as submit
import scenario_platform.control.jobs as jobs
import scenario_platform.control.classify_failure as classify_failure
assert callable(submit.handler)
assert callable(jobs.handler)
assert callable(classify_failure.handler)
leaked = [m for m in {_FORBIDDEN_AT_RUNTIME!r} if m in sys.modules]
assert not leaked, f"scientific modules leaked into the control plane: {{leaked}}"
import boto3
print(f"OK: three handlers imported; boto3 resolved from the Lambda "
      f"runtime itself ({{boto3.__file__}})")
"""


def verify(build_dir: Path, *, image: str = "public.ecr.aws/lambda/python:3.13") -> None:
    """Clean-room proof, not an assumption: run the exact three handlers this
    zip ships, in the real AWS Lambda Python 3.13 base image, using ONLY the
    zip's own extracted content on ``sys.path`` (the container has no other
    Python packages installed of its own besides what that base image
    already ships, which is what makes "boto3 resolved from the Lambda
    runtime itself" below a meaningful proof rather than an assumption
    about what AWS provides).
    """
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--entrypoint",
            "python",
            "-v",
            f"{build_dir}:/var/task:ro",
            image,
            "-c",
            _VERIFY_SCRIPT,
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout, end="")
    if result.returncode != 0:
        print(result.stderr, end="", file=sys.stderr)
        raise RuntimeError(
            "clean-room Lambda-runtime import verification failed -- see output above"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=REPO_ROOT / "build" / "control-plane.zip"
    )
    parser.add_argument("--python-version", default="3.13")
    parser.add_argument(
        "--skip-verify",
        action="store_true",
        help=(
            "Skip the Docker-based clean-room import check "
            "(only for environments with no Docker daemon)."
        ),
    )
    args = parser.parse_args(argv)
    build(
        output=args.output, python_version=args.python_version, skip_verify=args.skip_verify
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
