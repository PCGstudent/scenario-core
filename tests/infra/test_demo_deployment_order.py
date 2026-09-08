"""Pre-provision cleanup and server-side deadline regressions, offline."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2] / "infra" / "terraform"


def _text(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_cleanup_dependency_does_not_require_costly_resources():
    env = _text("envs/dev/main.tf")
    network = env[env.index('module "network"') : env.index('module "ci_oidc"')]
    cleanup = env[env.index('module "demo_killswitch"') :]
    assert "depends_on = [module.demo_killswitch]" in network
    assert "module.network." not in cleanup
    assert "module.worker_compute." not in cleanup
    assert all(name in cleanup for name in ("ecr-api", "ecr-dkr", "logs", "-probe"))
    assert "permissions_boundary_arn = module.ci_oidc.runtime_role_boundary_arn" in cleanup
    variables = _text("envs/dev/variables.tf")
    assert "demo_killswitch_enabled" not in variables
    assert "count  = var.demo_killswitch_enabled" not in cleanup


def test_role_deadlines_are_iam_denies():
    for name, role, action in (
        ("job_api", "api_submit", "states:StartExecution"),
        ("job_orchestrator", "sfn_orchestrator", "ecs:RunTask"),
    ):
        text = _text(f"modules/{name}/main.tf")
        start = text.index(f'data "aws_iam_policy_document" "{role}"')
        policy = text[start:]
        block = policy[policy.index('sid       = "DenyNewWorkAfterDemoDeadline"') :]
        assert 'effect    = "Deny"' in block[:500]
        assert f'actions   = ["{action}"]' in block[:500]
        assert 'test     = "DateGreaterThanEquals"' in block[:500]
        assert 'variable = "aws:CurrentTime"' in block[:500]


def test_cleanup_cannot_deregister_unrelated_tasks_or_touch_data():
    text = _text("modules/demo_killswitch/main.tf")
    start = text.index('data "aws_iam_policy_document" "cleanup"')
    end = text.index('resource "aws_iam_role_policy" "cleanup"')
    policy = text[start:end]
    assert "ecs:DeregisterTaskDefinition" not in policy
    import re

    granted = re.findall(r'"([a-z0-9]+:[A-Za-z*]+)"', policy)
    assert not any(
        action.startswith(("s3:", "dynamodb:", "kms:", "iam:")) for action in granted
    )
    delete = policy[policy.index('sid       = "DeleteOnlyTheseEndpoints"') :]
    assert 'variable = "ec2:ResourceTag/Name"' in delete[:600]
    assert text.count("permissions_boundary = var.permissions_boundary_arn") == 2

    boundary = _text("modules/ci_oidc/main.tf")
    for action in (
        "ec2:DescribeVpcEndpoints",
        "ec2:DeleteVpcEndpoints",
        "ecs:ListTasks",
        "ecs:StopTask",
        "ecs:DescribeTasks",
        "sns:Publish",
        "lambda:InvokeFunction",
    ):
        assert f'"{action}"' in boundary


def test_deploy_workflow_passes_immutable_image_and_mandatory_deadline():
    workflow = (ROOT.parents[1] / ".github/workflows/deploy-dev.yml").read_text(
        encoding="utf-8"
    )
    apply = workflow[workflow.index("  terraform-apply:") :]
    assert "needs: [guard, build-and-push]" in apply
    assert "needs.build-and-push.outputs.image_digest" in apply
    assert "python scripts/package_control_plane.py" in apply
    for variable in (
        "worker_image_digest",
        "control_plane_package_path",
        "alert_email",
        "demo_schedule_expression",
    ):
        assert f'-var="{variable}=' in apply


def test_lambda_zip_structurally_excludes_scientific_domain_sources():
    script = (ROOT.parents[1] / "scripts/package_control_plane.py").read_text(
        encoding="utf-8"
    )
    assert '_DOMAIN_FILES_NEEDED = ("__init__.py", "policies.py")' in script
    package_line = next(
        line for line in script.splitlines() if line.startswith("_BUNDLED_PACKAGES =")
    )
    assert '"domain"' not in package_line
