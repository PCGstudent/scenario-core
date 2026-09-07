"""Guards the architectural boundary in AGENTS.md invariant 28.

The quantitative core (``xtra_takehome``) and the pure domain layer
(``scenario_platform.domain``, once Phase 1 creates it) must stay
infrastructure-independent: no ``boto3``/``botocore`` import, and no reading
of AWS or other infrastructure configuration from the environment.

Ruff's TID251 banned-import rule expresses the import half of this for any
file it actually lints (see ``pyproject.toml``), but it does not scan
``src/xtra_takehome`` -- that package is excluded from Ruff's Phase 0 scope to
avoid an unrelated mass-formatting diff over the existing statistical
implementation. This test is therefore the boundary's real, always-active
enforcement, independent of lint configuration, and it also checks the half
Ruff cannot express at all (environment-variable coupling).

Static AST inspection is used deliberately rather than importing every
module: importing ``xtra_takehome.data`` or ``xtra_takehome.app.llm`` would
require fixtures for network access and optional dependencies (``yfinance``,
``openai``) that have nothing to do with what this test checks, and a parse
error is a stronger, more specific signal than an import failure would be.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Any of these as the literal argument to os.environ.get/os.getenv, or as a
# literal subscript key on os.environ, signals infrastructure configuration
# leaking into a layer that must not know it is being deployed anywhere.
# Prefix-matched on "AWS_" to catch the whole family of SDK/runtime variables
# without enumerating every one of them; the rest are runtime markers for
# other services this architecture uses (Lambda, ECS, Step Functions).
_AWS_ENV_PREFIXES = ("AWS_",)
_INFRA_ENV_DENYLIST = frozenset(
    {
        "LAMBDA_TASK_ROOT",
        "LAMBDA_RUNTIME_DIR",
        "ECS_CONTAINER_METADATA_URI",
        "ECS_CONTAINER_METADATA_URI_V4",
        "ECS_AGENT_URI",
        "DYNAMODB_TABLE",
        "DYNAMODB_TABLE_NAME",
        "STATE_MACHINE_ARN",
        "TASK_TOKEN",
    }
)

# xtra_takehome.app.llm reads OPENAI_API_KEY, an explicit, documented
# exception in the architecture plan (Section 26): an optional third-party
# key for the lab's natural-language layer, not infrastructure configuration.
# It intentionally does not match any pattern above and needs no special case.


def _iter_python_files(package_dir: Path) -> list[Path]:
    if not package_dir.is_dir():
        return []
    return sorted(package_dir.rglob("*.py"))


def _parse(path: Path) -> ast.Module:
    source = path.read_text(encoding="utf-8")
    return ast.parse(source, filename=str(path))


def _imported_root_modules(tree: ast.Module) -> set[str]:
    """Top-level module names this file imports, e.g. {"os", "boto3"}."""
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            roots.add(node.module.split(".")[0])
    return roots


def _environ_key_literals(tree: ast.Module) -> list[tuple[str, int]]:
    """Every string literal used as an os.environ/os.getenv key, with its line."""
    found: list[tuple[str, int]] = []

    def _is_os_environ_attr(node: ast.AST, attr: str) -> bool:
        return (
            isinstance(node, ast.Attribute)
            and node.attr == attr
            and isinstance(node.value, ast.Name)
            and node.value.id == "os"
        )

    for node in ast.walk(tree):
        # os.environ.get("KEY") / os.environ.get("KEY", default)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and _is_os_environ_attr(node.func.value, "environ")
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append((node.args[0].value, node.lineno))
        # os.getenv("KEY")
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "getenv"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "os"
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            found.append((node.args[0].value, node.lineno))
        # os.environ["KEY"]
        elif (
            isinstance(node, ast.Subscript)
            and _is_os_environ_attr(node.value, "environ")
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            found.append((node.slice.value, node.lineno))

    return found


def _is_infra_key(key: str) -> bool:
    return key.startswith(_AWS_ENV_PREFIXES) or key in _INFRA_ENV_DENYLIST


def _assert_package_is_clean(package_dir: Path, package_label: str) -> None:
    files = _iter_python_files(package_dir)
    assert files, f"expected at least one source file under {package_dir}"

    import_violations: list[str] = []
    env_violations: list[str] = []

    for path in files:
        tree = _parse(path)
        rel = path.relative_to(REPO_ROOT)

        roots = _imported_root_modules(tree)
        banned = roots & {"boto3", "botocore"}
        if banned:
            import_violations.append(f"{rel}: imports {sorted(banned)}")

        for key, lineno in _environ_key_literals(tree):
            if _is_infra_key(key):
                env_violations.append(f"{rel}:{lineno}: reads infra env var {key!r}")

    assert not import_violations, (
        f"{package_label} must not import boto3/botocore (AGENTS.md invariant 28):\n"
        + "\n".join(import_violations)
    )
    assert not env_violations, (
        f"{package_label} must not read infrastructure configuration from the "
        "environment (AGENTS.md invariant 28):\n" + "\n".join(env_violations)
    )


def test_quantitative_core_has_no_aws_coupling():
    _assert_package_is_clean(
        REPO_ROOT / "src" / "xtra_takehome", "the quantitative core (xtra_takehome)"
    )


def test_domain_layer_has_no_aws_coupling_when_it_exists():
    """Phase 1 creates scenario_platform.domain; it inherits this rule unchanged.

    Skipped rather than failed while the package does not exist yet, so this
    test starts enforcing the moment Phase 1 adds the directory -- with no
    further edit required here.
    """
    import pytest

    domain_dir = REPO_ROOT / "src" / "scenario_platform" / "domain"
    if not domain_dir.is_dir():
        pytest.skip("scenario_platform.domain does not exist yet (created in Phase 1)")
    _assert_package_is_clean(domain_dir, "the domain layer (scenario_platform.domain)")


def test_worker_layer_has_no_aws_coupling_when_it_exists():
    """Phase 2 creates scenario_platform.worker; it gets the same rule,
    permanently -- unlike scenario_platform.adapters (documented in the
    repository structure as "the ONLY place boto3 appears in the data
    plane," once Phase 3 adds s3_store.py/job_store.py there), the worker
    process itself is never meant to import boto3 directly. The
    control/data-plane split (Section 4) means a Phase-3 worker calls
    *into* ``adapters.s3_store``/``adapters.job_store`` as its own
    dependency boundary, never the AWS SDK itself -- so this check, unlike
    the domain one above, is not expected to ever need loosening.

    Skipped rather than failed while the package does not exist, exactly
    like the domain-layer test above.
    """
    import pytest

    worker_dir = REPO_ROOT / "src" / "scenario_platform" / "worker"
    if not worker_dir.is_dir():
        pytest.skip("scenario_platform.worker does not exist yet (created in Phase 2)")
    _assert_package_is_clean(worker_dir, "the worker CLI (scenario_platform.worker)")


def test_boto3_import_is_actually_detected():
    """Negative control: the AST scan must be able to fail.

    Without this, a typo in the walker (e.g. checking the wrong attribute)
    could pass silently forever because nothing in the real source trips it.
    """
    tree = ast.parse("import boto3\n")
    assert _imported_root_modules(tree) == {"boto3"}

    tree = ast.parse("from botocore.exceptions import ClientError\n")
    assert _imported_root_modules(tree) == {"botocore"}


def test_aws_env_var_read_is_actually_detected():
    """Negative control for the environment-variable half of the check."""
    tree = ast.parse('import os\nos.environ.get("AWS_REGION")\n')
    keys = [key for key, _ in _environ_key_literals(tree)]
    assert keys == ["AWS_REGION"]
    assert _is_infra_key("AWS_REGION")
    # An unrelated key (e.g. the lab's optional OpenAI key) must NOT be flagged.
    assert not _is_infra_key("OPENAI_API_KEY")
