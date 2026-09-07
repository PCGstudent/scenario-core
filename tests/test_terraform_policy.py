"""Custom Terraform policy checks (IMPLEMENTATION_PLAN.md Section 17).

These check STRUCTURAL properties tflint/checkov do not enforce -- the
exact rules this project's own IAM/network design imposes on itself:

* no aws_nat_gateway / aws_internet_gateway anywhere;
* no bare Action="*" and no broad service wildcard (s3:*, iam:*, dynamodb:*,
  ecs:*, kms:*, logs:*) in any IAM IDENTITY policy's Allow statement
  (aws_iam_role_policy / aws_iam_policy) -- runtime and deployment checked
  together here because, as written, neither carries any such grant; a
  service-wildcard Deny (e.g. the guardrails module's "iam:*" Deny) is a
  hardening statement, not an escalation, and is deliberately excluded;
* Resource="*" in such a statement only when its sid is enumerated in
  infra/terraform/policy/resource-star-allowlist.yaml;
* no iam:PassRole statement without an iam:PassedToService condition;
* every iam:CreateRole grant conditions on the project permissions
  boundary;
* every S3 bucket carries a public-access-block, KMS encryption and a
  policy (Section 17: "no S3 bucket without public-access-block, KMS
  encryption and a TLS-only policy");
* a dynamodb:PutItem/UpdateItem grant scoped to the model-registry table
  carries a dynamodb:LeadingKeys condition (Section 8.4/12.3/17) -- no
  such grant exists yet in Phase 3a (the calibrator/approver roles arrive
  in Phase 4), so this is a structural readiness check today, honestly
  reported as such; a synthetic negative-control test below proves the
  underlying assertion function actually rejects a violation, so the
  check is not the kind AGENTS.md invariant 14 warns against -- nothing
  can fail it *yet* only because nothing yet exercises it, not because it
  cannot fail.

Resource-based policies (S3 bucket policies, KMS key policies, ECR
repository policies, VPC endpoint policies) are a different category from
IAM identity policies -- see the allowlist file's own header comment --
and are not subject to the identity-policy checks below.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import hcl2
import pytest
import yaml

INFRA_ROOT = Path(__file__).parent.parent / "infra" / "terraform"
ALLOWLIST_PATH = INFRA_ROOT / "policy" / "resource-star-allowlist.yaml"

#: Section 17: "no broad service wildcards (s3:*, iam:*, dynamodb:*,
#: ecs:*, kms:*, logs:*) in any policy document."
BROAD_WILDCARDS = {"s3:*", "iam:*", "dynamodb:*", "ecs:*", "kms:*", "logs:*"}

#: Terraform resources that attach an aws_iam_policy_document's rendered
#: JSON as an IAM IDENTITY policy (as opposed to a resource-based policy
#: like an S3/KMS/ECR/endpoint policy).
IDENTITY_POLICY_RESOURCE_TYPES = ("aws_iam_role_policy", "aws_iam_policy")


def _all_tf_files() -> list[Path]:
    return sorted(INFRA_ROOT.rglob("*.tf"))


def _unquote(value: Any) -> Any:
    """python-hcl2 leaves literal string tokens double-quoted in some
    positions (e.g. inside nested statement blocks); strip a single
    surrounding pair if present, recursively for lists."""
    if isinstance(value, str) and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    if isinstance(value, list):
        return [_unquote(v) for v in value]
    return value


def _load_tf(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        return hcl2.load(f)


def _iter_blocks(parsed: dict[str, Any], top_key: str):
    """Yield (block_type_or_name, inner_dict) for every top-level block of
    kind `top_key` ("resource" or "data") in one parsed file."""
    for block in parsed.get(top_key, []):
        for kind, named in block.items():
            for name, body in named.items():
                yield _unquote(kind), _unquote(name), body


def _policy_documents_by_address() -> dict[str, list[dict[str, Any]]]:
    """Map "data.aws_iam_policy_document.NAME" -> its list of statement
    dicts, across every file in the tree (Terraform allows same-module
    cross-file references, so this is built globally, not per-file)."""
    registry: dict[str, list[dict[str, Any]]] = {}
    for path in _all_tf_files():
        parsed = _load_tf(path)
        for kind, name, body in _iter_blocks(parsed, "data"):
            if kind != "aws_iam_policy_document":
                continue
            statements = _unquote(body.get("statement", []))
            registry[f"data.aws_iam_policy_document.{name}"] = statements
    return registry


def _identity_policy_statements() -> list[tuple[str, str, dict[str, Any]]]:
    """Yield (source_file, resource_address, statement) for every
    statement inside a policy document attached to an
    aws_iam_role_policy/aws_iam_policy resource anywhere in the tree."""
    doc_registry = _policy_documents_by_address()
    results: list[tuple[str, str, dict[str, Any]]] = []
    for path in _all_tf_files():
        parsed = _load_tf(path)
        for kind, name, body in _iter_blocks(parsed, "resource"):
            if kind not in IDENTITY_POLICY_RESOURCE_TYPES:
                continue
            policy_expr = _unquote(body.get("policy"))
            if not isinstance(policy_expr, str):
                continue
            match = re.search(r"data\.aws_iam_policy_document\.(\w+)", policy_expr)
            if not match:
                continue
            address = f"data.aws_iam_policy_document.{match.group(1)}"
            statements = doc_registry.get(address, [])
            for statement in statements:
                results.append(
                    (
                        str(path.relative_to(INFRA_ROOT.parent.parent)),
                        f"{kind}.{name}",
                        statement,
                    )
                )
    return results


def _load_allowlist() -> dict[str, Any]:
    with ALLOWLIST_PATH.open(encoding="utf-8") as f:
        return yaml.safe_load(f)


# ---------------------------------------------------------------------------
# Assertion functions, unit-tested directly below with synthetic input so
# each is proven to actually reject a violation (AGENTS.md invariant 14,
# ported to this policy-check module) -- not just exercised against
# whatever this repository's own .tf files happen to contain today.
# ---------------------------------------------------------------------------


def _statement_actions(statement: dict[str, Any]) -> list[str]:
    return [str(a) for a in _unquote(statement.get("actions", []))]


def _statement_resources(statement: dict[str, Any]) -> list[str]:
    return [str(r) for r in _unquote(statement.get("resources", []))]


def _statement_effect(statement: dict[str, Any]) -> str:
    return str(_unquote(statement.get("effect", "Allow")))


def _statement_conditions(statement: dict[str, Any]) -> list[dict[str, Any]]:
    return [_unquote(c) for c in statement.get("condition", [])]


def assert_no_bare_or_broad_wildcard_action(statement: dict[str, Any]) -> None:
    if _statement_effect(statement) != "Allow":
        return
    actions = _statement_actions(statement)
    assert "*" not in actions, f'bare Action="*" in Allow statement: {statement.get("sid")}'
    broad = BROAD_WILDCARDS & set(actions)
    assert not broad, (
        f"broad service wildcard {broad} in Allow statement: {statement.get('sid')}"
    )


def assert_resource_star_is_allowlisted(
    statement: dict[str, Any], allowlisted_sids: set[str]
) -> None:
    if _statement_effect(statement) != "Allow":
        return
    if _statement_resources(statement) != ["*"]:
        return
    sid = str(_unquote(statement.get("sid", "")))
    assert sid in allowlisted_sids, (
        f'Allow statement with Resource="*" (sid={sid!r}) is not listed in {ALLOWLIST_PATH}'
    )


def assert_passrole_has_passed_to_service_condition(statement: dict[str, Any]) -> None:
    actions = _statement_actions(statement)
    if "iam:PassRole" not in actions:
        return
    conditions = _statement_conditions(statement)
    has_condition = any(
        str(_unquote(c.get("variable"))) == "iam:PassedToService" for c in conditions
    )
    assert has_condition, (
        f"iam:PassRole statement without iam:PassedToService: {statement.get('sid')}"
    )


def assert_dynamodb_registry_write_has_leading_keys_condition(
    statement: dict[str, Any],
) -> None:
    if _statement_effect(statement) != "Allow":
        return
    actions = set(_statement_actions(statement))
    if not ({"dynamodb:PutItem", "dynamodb:UpdateItem"} & actions):
        return
    resources = _statement_resources(statement)
    if not any("model-registry" in r for r in resources):
        return
    conditions = _statement_conditions(statement)
    has_condition = any(
        str(_unquote(c.get("variable"))) == "dynamodb:LeadingKeys" for c in conditions
    )
    assert has_condition, (
        f"dynamodb:PutItem/UpdateItem on the model-registry table without a "
        f"dynamodb:LeadingKeys condition: {statement.get('sid')}"
    )


# ---------------------------------------------------------------------------
# Repository-wide checks
# ---------------------------------------------------------------------------


def test_no_nat_gateway_or_internet_gateway_anywhere():
    for path in _all_tf_files():
        text = path.read_text(encoding="utf-8")
        assert 'resource "aws_nat_gateway"' not in text, f"{path}: aws_nat_gateway found"
        assert 'resource "aws_internet_gateway"' not in text, (
            f"{path}: aws_internet_gateway found"
        )


def test_no_bare_or_broad_wildcard_action_in_any_identity_policy():
    statements = _identity_policy_statements()
    assert statements, "no IAM identity-policy statements found -- check the parser"
    for _source_file, _address, statement in statements:
        assert_no_bare_or_broad_wildcard_action(statement)


def test_resource_star_in_identity_policies_is_allowlisted():
    allowlist = _load_allowlist()
    allowlisted_sids = {
        entry["sid"] for entry in [*allowlist["runtime"], *allowlist["deployment"]]
    }
    statements = _identity_policy_statements()
    for _source_file, _address, statement in statements:
        assert_resource_star_is_allowlisted(statement, allowlisted_sids)


def test_allowlist_separates_runtime_and_deployment_roles():
    """Section 17: 'runtime and deployment roles are checked separately,
    with the deployment allow-list broader by design.'"""
    allowlist = _load_allowlist()
    assert "runtime" in allowlist and isinstance(allowlist["runtime"], list)
    assert "deployment" in allowlist and isinstance(allowlist["deployment"], list)
    for entry in [*allowlist["runtime"], *allowlist["deployment"]]:
        assert entry.get("justification"), (
            f"allowlist entry missing a justification: {entry}"
        )
        assert entry.get("role"), f"allowlist entry missing the role it applies to: {entry}"


def test_every_passrole_statement_has_passed_to_service_condition():
    statements = _identity_policy_statements()
    passrole_statements = [
        s for _f, _a, s in statements if "iam:PassRole" in _statement_actions(s)
    ]
    assert passrole_statements, "no iam:PassRole statement found -- check the parser"
    for statement in passrole_statements:
        assert_passrole_has_passed_to_service_condition(statement)


def test_create_role_grant_conditions_on_permissions_boundary():
    """Section 13.1/17: 'a permissions boundary attached to every role the
    deployment identity creates' -- structurally enforced as an
    iam:PermissionsBoundary condition on the iam:CreateRole grant."""
    statements = _identity_policy_statements()
    create_role_statements = [
        s for _f, _a, s in statements if "iam:CreateRole" in _statement_actions(s)
    ]
    assert create_role_statements, "no iam:CreateRole statement found -- check the parser"
    for statement in create_role_statements:
        conditions = _statement_conditions(statement)
        has_boundary_condition = any(
            str(_unquote(c.get("variable"))) == "iam:PermissionsBoundary"
            for c in conditions
        )
        sid = statement.get("sid")
        assert has_boundary_condition, f"CreateRole missing PermissionsBoundary cond: {sid}"


def test_dynamodb_registry_writes_have_leading_keys_condition():
    statements = _identity_policy_statements()
    for _source_file, _address, statement in statements:
        assert_dynamodb_registry_write_has_leading_keys_condition(statement)


def test_every_s3_bucket_has_public_access_block_and_encryption_and_policy():
    """Section 17: 'no S3 bucket without public-access-block, KMS
    encryption and a TLS-only policy.' Matches companion resources by
    their `bucket = aws_s3_bucket.<name>.id` reference, which is how every
    bucket in this tree associates its public-access-block/SSE/policy
    resources."""
    bucket_addresses: set[str] = set()
    public_access_block_targets: set[str] = set()
    encryption_targets: set[str] = set()
    policy_targets: set[str] = set()

    for path in _all_tf_files():
        parsed = _load_tf(path)
        for kind, name, body in _iter_blocks(parsed, "resource"):
            if kind == "aws_s3_bucket":
                bucket_addresses.add(name)
                continue
            bucket_ref = _unquote(body.get("bucket"))
            if not isinstance(bucket_ref, str):
                continue
            match = re.search(r"aws_s3_bucket\.(\w+)\.id", bucket_ref)
            if not match:
                continue
            target = match.group(1)
            if kind == "aws_s3_bucket_public_access_block":
                public_access_block_targets.add(target)
            elif kind == "aws_s3_bucket_server_side_encryption_configuration":
                encryption_targets.add(target)
            elif kind == "aws_s3_bucket_policy":
                policy_targets.add(target)

    assert bucket_addresses, "no aws_s3_bucket resources found -- check the parser"
    for bucket in bucket_addresses:
        assert bucket in public_access_block_targets, f"{bucket}: no public access block"
        assert bucket in encryption_targets, f"{bucket}: no SSE configuration"
        assert bucket in policy_targets, (
            f"{bucket}: no bucket policy (expected a TLS-only deny)"
        )


# ---------------------------------------------------------------------------
# Negative controls: prove each assertion function actually rejects a
# violation, using synthetic statements -- never the repository's own
# (already-compliant) .tf files, per AGENTS.md invariant 14 ported to this
# module's docstring.
# ---------------------------------------------------------------------------


def test_negative_control_bare_wildcard_action_is_rejected():
    with pytest.raises(AssertionError):
        assert_no_bare_or_broad_wildcard_action(
            {"sid": "synthetic", "effect": "Allow", "actions": ["*"]}
        )


def test_negative_control_broad_service_wildcard_is_rejected():
    with pytest.raises(AssertionError):
        assert_no_bare_or_broad_wildcard_action(
            {"sid": "synthetic", "effect": "Allow", "actions": ["s3:*"]}
        )


def test_negative_control_deny_statement_wildcard_action_is_permitted():
    # A Deny is a hardening statement, not an escalation -- must NOT raise.
    assert_no_bare_or_broad_wildcard_action(
        {"sid": "synthetic", "effect": "Deny", "actions": ["iam:*"]}
    )


def test_negative_control_unlisted_resource_star_is_rejected():
    with pytest.raises(AssertionError):
        assert_resource_star_is_allowlisted(
            {
                "sid": "NotInTheAllowlist",
                "effect": "Allow",
                "resources": ["*"],
            },
            allowlisted_sids=set(),
        )


def test_negative_control_passrole_without_condition_is_rejected():
    with pytest.raises(AssertionError):
        assert_passrole_has_passed_to_service_condition(
            {"sid": "synthetic", "actions": ["iam:PassRole"], "condition": []}
        )


def test_negative_control_dynamodb_registry_write_without_leading_keys_is_rejected():
    with pytest.raises(AssertionError):
        assert_dynamodb_registry_write_has_leading_keys_condition(
            {
                "sid": "synthetic",
                "effect": "Allow",
                "actions": ["dynamodb:PutItem"],
                "resources": [
                    "arn:aws:dynamodb:eu-west-1:123456789012:table/4xtra-dev-model-registry"
                ],
                "condition": [],
            }
        )
