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


#: Actions AWS's own IAM User Guide example does NOT bundle under an
#: iam:PermissionsBoundary condition (GetRolePolicy, ListRolePolicies,
#: UpdateUser, DeleteUser all sit in that example's separate, unconditioned
#: statement) -- applying the condition to these anyway would silently
#: deny them for any role that does not (yet) carry the exact boundary,
#: which is a functional bug, not extra safety.
IAM_ACTIONS_INELIGIBLE_FOR_BOUNDARY_CONDITION = {
    "iam:GetRole",
    "iam:UpdateRole",
    "iam:DeleteRole",
    "iam:TagRole",
    "iam:UntagRole",
    "iam:GetRolePolicy",
    "iam:ListRolePolicies",
    "iam:ListAttachedRolePolicies",
    "iam:CreatePolicy",
    "iam:DeletePolicy",
    "iam:GetPolicy",
    "iam:GetPolicyVersion",
    "iam:CreatePolicyVersion",
    "iam:DeletePolicyVersion",
    "iam:ListPolicyVersions",
    "iam:TagPolicy",
    "iam:UntagPolicy",
}


def assert_no_boundary_condition_on_ineligible_actions(statement: dict[str, Any]) -> None:
    actions = set(_statement_actions(statement))
    if not (actions & IAM_ACTIONS_INELIGIBLE_FOR_BOUNDARY_CONDITION):
        return
    conditions = _statement_conditions(statement)
    has_boundary_condition = any(
        str(_unquote(c.get("variable"))) == "iam:PermissionsBoundary" for c in conditions
    )
    assert not has_boundary_condition, (
        f"iam:PermissionsBoundary condition applied to an action that does not carry "
        f"that context key (GetRole/policy-reads/etc): {statement.get('sid')}"
    )


def assert_ecr_layer_bucket_statement_is_minimal(statement: dict[str, Any]) -> None:
    resources = _statement_resources(statement)
    if not any("starport-layer-bucket" in r for r in resources):
        return
    actions = set(_statement_actions(statement))
    assert actions == {"s3:GetObject"}, (
        f"ECR layer-bucket statement must grant only s3:GetObject (AWS's documented "
        f"minimum), got {actions}"
    )
    for r in resources:
        assert r.startswith("arn:aws:s3:::prod-") and r.endswith(
            "-starport-layer-bucket/*"
        ), (
            f"ECR layer-bucket resource pattern does not match AWS's documented "
            f"arn:aws:s3:::prod-{{region}}-starport-layer-bucket/*: {r}"
        )


def assert_delete_object_is_scoped_to_lock_file_only(statement: dict[str, Any]) -> None:
    if _statement_effect(statement) != "Allow":
        return
    if "s3:DeleteObject" not in _statement_actions(statement):
        return
    for r in _statement_resources(statement):
        assert r.endswith(".tflock"), (
            f"s3:DeleteObject granted on a non-.tflock resource -- HashiCorp's own S3 "
            f"native-locking docs scope DeleteObject to the lock file only: {r} "
            f"(sid={statement.get('sid')})"
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


def test_permissions_boundary_condition_not_applied_indiscriminately():
    """The concrete finding this guards against: an earlier revision
    bundled iam:GetRole, policy reads, and other calls that do not carry
    the iam:PermissionsBoundary request-context key into the SAME
    condition as iam:CreateRole -- which, per AWS's own IAM User Guide
    example, silently denies those calls entirely for a role that does
    not yet satisfy the condition (a missing context key makes
    StringEquals evaluate false)."""
    statements = _identity_policy_statements()
    assert statements, "no IAM identity-policy statements found -- check the parser"
    for _source_file, _address, statement in statements:
        assert_no_boundary_condition_on_ineligible_actions(statement)


def test_ecr_layer_bucket_access_is_present_and_minimally_scoped():
    """Finding 1: the S3 gateway endpoint must grant s3:GetObject on
    AWS's documented ECR image-layer bucket, and nothing broader."""
    registry = _policy_documents_by_address()
    statements = registry.get("data.aws_iam_policy_document.s3_endpoint")
    assert statements, "s3_endpoint policy document not found -- check the parser"
    layer_bucket_statements = [
        s
        for s in statements
        if any("starport-layer-bucket" in r for r in _statement_resources(s))
    ]
    assert layer_bucket_statements, (
        "no ECR layer-bucket statement found in the S3 gateway endpoint policy"
    )
    for statement in layer_bucket_statements:
        assert_ecr_layer_bucket_statement_is_minimal(statement)


def test_s3_gateway_endpoint_policy_grants_no_blanket_resource_star():
    """Other third-party buckets must stay unreachable through this
    endpoint: no statement in it may fall back to Resource="*"."""
    registry = _policy_documents_by_address()
    statements = registry.get("data.aws_iam_policy_document.s3_endpoint")
    assert statements, "s3_endpoint policy document not found -- check the parser"
    for statement in statements:
        sid = statement.get("sid")
        resources = _statement_resources(statement)
        assert resources != ["*"], f"S3 endpoint policy grants Resource star: {sid}"


def test_state_delete_object_is_scoped_to_lock_file_only():
    """Finding 3: s3:DeleteObject must never be grantable on the state
    object itself, only on its .tflock lock file."""
    statements = _identity_policy_statements()
    delete_object_statements = [
        s for _f, _a, s in statements if "s3:DeleteObject" in _statement_actions(s)
    ]
    assert delete_object_statements, (
        "no s3:DeleteObject statement found -- check the parser"
    )
    for statement in delete_object_statements:
        assert_delete_object_is_scoped_to_lock_file_only(statement)


def test_gha_ci_dev_cannot_modify_its_own_policies_or_trust():
    """Finding 2's self-escalation concern: gha-ci-dev's own ARN matches
    the project-prefix pattern its own Allow statements use, so an
    explicit Deny -- independent of that structural boundary-condition
    argument -- must close PutRolePolicy/AttachRolePolicy/
    DetachRolePolicy/DeleteRolePolicy/DeleteRole/UpdateAssumeRolePolicy
    on itself specifically."""
    statements = _identity_policy_statements()
    deny_self_statements = [
        s
        for _f, _a, s in statements
        if str(_unquote(s.get("sid", ""))) == "DenySelfPolicyModification"
    ]
    assert deny_self_statements, "DenySelfPolicyModification guardrail statement not found"
    statement = deny_self_statements[0]
    assert _statement_effect(statement) == "Deny"
    actions = set(_statement_actions(statement))
    required = {
        "iam:PutRolePolicy",
        "iam:AttachRolePolicy",
        "iam:DetachRolePolicy",
        "iam:DeleteRolePolicy",
        "iam:DeleteRole",
        "iam:UpdateAssumeRolePolicy",
    }
    assert required <= actions, (
        f"DenySelfPolicyModification missing actions: {required - actions}"
    )
    resources = _statement_resources(statement)
    assert any("aws_iam_role.gha_ci_dev.arn" in r for r in resources), (
        f"DenySelfPolicyModification not scoped to gha-ci-dev's own ARN: {resources}"
    )


def test_boundary_replacement_and_removal_actions_are_never_allowed():
    """Prevent unauthorized removal or replacement of a role's boundary:
    iam:PutRolePermissionsBoundary/DeleteRolePermissionsBoundary must
    never appear in any Allow statement anywhere."""
    statements = _identity_policy_statements()
    for _source_file, _address, statement in statements:
        if _statement_effect(statement) != "Allow":
            continue
        actions = set(_statement_actions(statement))
        forbidden = {
            "iam:PutRolePermissionsBoundary",
            "iam:DeleteRolePermissionsBoundary",
        } & actions
        assert not forbidden, f"boundary-replacement action granted by Allow: {forbidden}"


def test_boundary_policy_object_cannot_be_edited_by_gha_ci_dev():
    """AWS's own IAM User Guide worked example calls this
    "NoBoundaryPolicyEdit": without it, gha-ci-dev's own policy-object
    management grant would let it rewrite the boundary's content to be
    maximally permissive, defeating the CreateRole boundary condition
    entirely."""
    statements = _identity_policy_statements()
    deny_edit_statements = [
        s
        for _f, _a, s in statements
        if str(_unquote(s.get("sid", ""))) == "DenyBoundaryPolicyEdit"
    ]
    assert deny_edit_statements, "DenyBoundaryPolicyEdit guardrail statement not found"
    statement = deny_edit_statements[0]
    assert _statement_effect(statement) == "Deny"
    actions = set(_statement_actions(statement))
    required = {
        "iam:CreatePolicyVersion",
        "iam:DeletePolicy",
        "iam:DeletePolicyVersion",
        "iam:SetDefaultPolicyVersion",
    }
    assert required <= actions, (
        f"DenyBoundaryPolicyEdit missing actions: {required - actions}"
    )
    resources = _statement_resources(statement)
    assert any("runtime_role_boundary" in r for r in resources), (
        f"DenyBoundaryPolicyEdit is not scoped to the boundary policy itself: {resources}"
    )


def test_probe_runtime_roles_carry_the_permissions_boundary():
    """Finding 2: 'Ensure probe runtime roles have the intended
    boundary.' Without permissions_boundary set, gha-ci-dev's own
    boundary-conditioned iam:CreateRole grant could never create these
    roles at all (the condition key would be absent from the request)."""
    boundary_protected_roles: dict[str, str] = {}
    for path in _all_tf_files():
        parsed = _load_tf(path)
        for kind, name, body in _iter_blocks(parsed, "resource"):
            if kind != "aws_iam_role":
                continue
            boundary_expr = _unquote(body.get("permissions_boundary"))
            if isinstance(boundary_expr, str) and "runtime_role_boundary" in boundary_expr:
                boundary_protected_roles[name] = boundary_expr

    probe_roles = {"probe_execution", "probe_task"}
    missing = probe_roles - set(boundary_protected_roles)
    assert not missing, f"probe role(s) missing permissions_boundary: {missing}"


def test_probe_runtime_role_policies_are_covered_by_the_boundary():
    """Finding 2: 'Ensure ... the boundary actually permits their
    required operations.' Generalises the concrete ecr:DescribeRepositories
    gap this PR fixed into a structural check: every action a
    boundary-protected role's OWN inline policy grants must also appear
    in the boundary policy's own action list, or the boundary caps it
    below what the role's policy grants -- exactly the bug that would
    have made the probe's ECR reachability check fail with AccessDenied
    despite looking correctly authorised."""
    doc_registry = _policy_documents_by_address()
    boundary_actions: set[str] = set()
    for statement in doc_registry.get(
        "data.aws_iam_policy_document.runtime_role_boundary", []
    ):
        if _statement_effect(statement) == "Allow":
            boundary_actions.update(_statement_actions(statement))
    assert boundary_actions, (
        "runtime_role_boundary policy document not found -- check the parser"
    )

    # Map role resource name -> its own granted actions, by finding every
    # aws_iam_role_policy whose `role` argument references that role.
    role_own_actions: dict[str, set[str]] = {"probe_execution": set(), "probe_task": set()}
    for path in _all_tf_files():
        parsed = _load_tf(path)
        for kind, _name, body in _iter_blocks(parsed, "resource"):
            if kind != "aws_iam_role_policy":
                continue
            role_ref = _unquote(body.get("role"))
            if not isinstance(role_ref, str):
                continue
            match = re.search(r"aws_iam_role\.(\w+)\.(?:id|arn|name)", role_ref)
            if not match or match.group(1) not in role_own_actions:
                continue
            policy_expr = _unquote(body.get("policy"))
            doc_match = re.search(r"data\.aws_iam_policy_document\.(\w+)", str(policy_expr))
            if not doc_match:
                continue
            for statement in doc_registry.get(
                f"data.aws_iam_policy_document.{doc_match.group(1)}", []
            ):
                if _statement_effect(statement) == "Allow":
                    role_own_actions[match.group(1)].update(_statement_actions(statement))

    for role_name, own_actions in role_own_actions.items():
        assert own_actions, f"no actions found for {role_name} -- check the parser"
        uncovered = own_actions - boundary_actions
        assert not uncovered, (
            f"{role_name}'s own policy grants actions the permissions boundary does not "
            f"permit (would fail with AccessDenied despite the role's own policy "
            f"appearing to allow it): {uncovered}"
        )


def _statements_granting_action(
    statements: list[tuple[str, str, dict[str, Any]]], action: str
) -> list[dict[str, Any]]:
    """Statements whose action list contains EXACTLY this one action name
    -- used to check one action's own resource-scoping in isolation, so a
    statement that happens to bundle it with a differently-scopable
    action cannot hide a mismatch behind the other's correctness."""
    return [s for _f, _a, s in statements if action in _statement_actions(s)]


def test_ecs_register_task_definition_is_resource_scoped():
    """ecs:RegisterTaskDefinition's own row in the AWS Service
    Authorization Reference for ECS (list_ecs.html) lists resource type
    "task-definition*" (required) -- it DOES support resource-level
    scoping, unlike DeregisterTaskDefinition/DescribeTaskDefinition/
    ListTaskDefinitions (checked separately below, and verified
    individually rather than assumed to share one blanket ECS
    limitation)."""
    statements = _identity_policy_statements()
    matching = _statements_granting_action(statements, "ecs:RegisterTaskDefinition")
    assert matching, "no ecs:RegisterTaskDefinition statement found -- check the parser"
    unscopable_siblings = {
        "ecs:DeregisterTaskDefinition",
        "ecs:DescribeTaskDefinition",
        "ecs:ListTaskDefinitions",
    }
    for statement in matching:
        resources = _statement_resources(statement)
        assert resources != ["*"], (
            'ecs:RegisterTaskDefinition granted with Resource="*" -- it supports '
            "resource-level scoping to the task-definition family and must not fall "
            "back to a bare wildcard"
        )
        assert any(":task-definition/" in r for r in resources), (
            f"ecs:RegisterTaskDefinition has no task-definition-scoped resource in "
            f"its resources list: {resources}"
        )
        bundled = set(_statement_actions(statement)) & unscopable_siblings
        assert not bundled, (
            f"ecs:RegisterTaskDefinition bundled in the same statement as "
            f'Resource="*"-only actions: {bundled}'
        )


def test_ecs_task_definition_readonly_and_deregister_are_genuinely_unscopable():
    """DeregisterTaskDefinition, DescribeTaskDefinition and
    ListTaskDefinitions each individually list no resource type in the
    AWS Service Authorization Reference for ECS -- Resource="*" is the
    verified scoping for these three specifically, checked one action at
    a time rather than assumed from RegisterTaskDefinition's presence
    (which is resource-scoped -- see the test above) or from any other
    ECS action's own compatibility."""
    statements = _identity_policy_statements()
    for action in (
        "ecs:DeregisterTaskDefinition",
        "ecs:DescribeTaskDefinition",
        "ecs:ListTaskDefinitions",
    ):
        matching = _statements_granting_action(statements, action)
        assert matching, f"no statement grants {action} -- check the parser"
        for statement in matching:
            assert _statement_resources(statement) == ["*"], (
                f'{action} is not granted with Resource="*" -- if AWS has since '
                f"added resource-level support, verify against the current Service "
                f"Authorization Reference and update the allowlist/comments: "
                f"{statement.get('sid')}"
            )


def test_logs_describe_log_groups_is_isolated_in_its_own_resource_star_statement():
    """logs:DescribeLogGroups' own row in the AWS Service Authorization
    Reference for CloudWatch Logs (list_logs.html) lists no resource type
    at all -- verified individually, not assumed from the other
    log-group-management actions it used to share a statement with."""
    statements = _identity_policy_statements()
    matching = _statements_granting_action(statements, "logs:DescribeLogGroups")
    assert matching, "no logs:DescribeLogGroups statement found -- check the parser"
    for statement in matching:
        assert _statement_resources(statement) == ["*"], (
            f'logs:DescribeLogGroups is not granted with Resource="*": '
            f"{statement.get('sid')}"
        )
        actions = set(_statement_actions(statement))
        assert actions == {"logs:DescribeLogGroups"}, (
            f"logs:DescribeLogGroups must be alone in its own statement, not bundled "
            f"with resource-scoped log-group actions: {actions}"
        )


def test_other_probe_log_group_actions_remain_resource_scoped():
    """The remaining CloudWatch Logs log-group-management actions each
    individually list "log-group" as a supported resource type in the AWS
    Service Authorization Reference and must stay scoped to the project's
    log-group ARN pattern -- verified one action at a time, so
    logs:DescribeLogGroups' Resource="*" fix cannot have silently widened
    any of these too."""
    statements = _identity_policy_statements()
    resource_scoped_actions = {
        "logs:CreateLogGroup",
        "logs:DeleteLogGroup",
        "logs:PutRetentionPolicy",
        "logs:AssociateKmsKey",
        "logs:TagResource",
        "logs:UntagResource",
        "logs:ListTagsForResource",
        "logs:TagLogGroup",
        "logs:UntagLogGroup",
        "logs:ListTagsLogGroup",
    }
    for action in resource_scoped_actions:
        matching = _statements_granting_action(statements, action)
        assert matching, f"no statement grants {action} -- check the parser"
        for statement in matching:
            resources = _statement_resources(statement)
            assert resources != ["*"], (
                f'{action} granted with Resource="*" -- it supports log-group '
                f"resource scoping and must not fall back to a bare wildcard"
            )
            # `any`, not `all`: a statement may legitimately grant several
            # actions across several resource types at once (e.g. the
            # permissions boundary's combined data-plane statement) --
            # what matters for THIS action is that a log-group-scoped ARN
            # is among its resources, not that every resource in a shared
            # list happens to be one.
            assert any("log-group:" in r for r in resources), (
                f"{action} has no log-group-scoped resource in its resources list: "
                f"{resources}"
            )


def test_ec2_create_actions_are_resource_scoped_not_resource_star():
    """Corrects the finding that an earlier revision inaccurately claimed
    EC2 VPC-family Create actions cannot support resource-level
    permissions. They can (verified against the AWS Service Authorization
    Reference and AWS's tag-based-access-control guidance); only the pure
    Describe*/List* actions genuinely cannot."""
    statements = _identity_policy_statements()
    create_actions = {
        "ec2:CreateVpc",
        "ec2:CreateSubnet",
        "ec2:CreateSecurityGroup",
        "ec2:CreateRouteTable",
        "ec2:CreateVpcEndpoint",
    }
    matching = [
        s for _f, _a, s in statements if set(_statement_actions(s)) & create_actions
    ]
    assert matching, "no EC2 create-action statement found -- check the parser"
    for statement in matching:
        assert _statement_resources(statement) != ["*"], (
            f'EC2 create action granted with Resource="*" -- these actions support '
            f"resource-type ARN scoping and must not fall back to a bare wildcard: "
            f"{statement.get('sid')}"
        )


def test_deploy_workflow_does_not_interpolate_input_directly_into_shell():
    """Finding 4: `${{ inputs.confirm }}` (or any `inputs.*`/`github.event.*`
    expression) must never appear directly inside a `run:` block's shell
    source -- GitHub substitutes it as literal text before bash runs,
    which is a documented script-injection vector for any value
    containing shell metacharacters. The GitHub-documented mitigation
    (pass through `env:`, reference the resulting variable) is what this
    checks is actually in place. Checks the parsed `run:` step values
    specifically, not the whole file text -- an explanatory comment
    elsewhere in the workflow is allowed to quote the vulnerable pattern
    as an example of what not to do."""
    workflow_path = (
        Path(__file__).parent.parent / ".github" / "workflows" / "deploy-dev.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    run_steps_checked = 0
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            run_value = step.get("run")
            if not isinstance(run_value, str):
                continue
            run_steps_checked += 1
            injected = "${{ inputs." in run_value or "${{ github.event." in run_value
            assert not injected, f"run step interpolates untrusted input: {run_value!r}"
    assert run_steps_checked > 0, "no `run:` steps found -- check the parser"


def test_deploy_workflow_guard_job_has_no_oidc_permission():
    """Finding 4: id-token: write must be scoped to the jobs that actually
    authenticate to AWS -- the guard job needs no OIDC permission at
    all."""
    workflow_path = (
        Path(__file__).parent.parent / ".github" / "workflows" / "deploy-dev.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    guard_permissions = workflow["jobs"]["guard"].get("permissions", {})
    assert guard_permissions.get("id-token") != "write", (
        "the guard job carries id-token: write but performs no AWS authentication"
    )
    for job_name in ("build-and-push", "terraform-apply"):
        assert (
            workflow["jobs"][job_name].get("permissions", {}).get("id-token") == "write"
        ), f"{job_name} authenticates to AWS via OIDC but lacks id-token: write"


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


def test_negative_control_boundary_condition_on_get_role_is_rejected():
    with pytest.raises(AssertionError):
        assert_no_boundary_condition_on_ineligible_actions(
            {
                "sid": "synthetic",
                "actions": ["iam:GetRole"],
                "condition": [
                    {"variable": "iam:PermissionsBoundary", "test": "StringEquals"}
                ],
            }
        )


def test_negative_control_boundary_condition_on_create_role_is_permitted():
    # CreateRole IS eligible -- must NOT raise.
    assert_no_boundary_condition_on_ineligible_actions(
        {
            "sid": "synthetic",
            "actions": ["iam:CreateRole"],
            "condition": [{"variable": "iam:PermissionsBoundary", "test": "StringEquals"}],
        }
    )


def test_negative_control_broad_ecr_layer_bucket_grant_is_rejected():
    with pytest.raises(AssertionError):
        assert_ecr_layer_bucket_statement_is_minimal(
            {
                "sid": "synthetic",
                "actions": ["s3:GetObject", "s3:ListBucket"],
                "resources": ["arn:aws:s3:::prod-eu-west-1-starport-layer-bucket/*"],
            }
        )


def test_negative_control_wrong_layer_bucket_arn_pattern_is_rejected():
    with pytest.raises(AssertionError):
        assert_ecr_layer_bucket_statement_is_minimal(
            {
                "sid": "synthetic",
                "actions": ["s3:GetObject"],
                "resources": ["arn:aws:s3:::prod-eu-west-1-starport-layer-bucket"],
            }
        )


def test_negative_control_delete_object_on_state_object_is_rejected():
    with pytest.raises(AssertionError):
        assert_delete_object_is_scoped_to_lock_file_only(
            {
                "sid": "synthetic",
                "effect": "Allow",
                "actions": ["s3:DeleteObject"],
                "resources": ["arn:aws:s3:::bucket/envs/dev/terraform.tfstate"],
            }
        )


def test_negative_control_delete_object_on_lock_file_is_permitted():
    # Scoped to the .tflock object -- must NOT raise.
    assert_delete_object_is_scoped_to_lock_file_only(
        {
            "sid": "synthetic",
            "effect": "Allow",
            "actions": ["s3:DeleteObject"],
            "resources": ["arn:aws:s3:::bucket/envs/dev/terraform.tfstate.tflock"],
        }
    )
