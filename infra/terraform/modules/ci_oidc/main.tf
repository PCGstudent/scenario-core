# GitHub OIDC provider + the DEV deploy role, gha-ci-dev. No long-lived AWS
# access key exists in CI, in either account (Section 13.2). This module
# creates DEV's role only -- PROD's gha-deploy-prod (pinned to
# `environment:prod` rather than `ref:refs/heads/main`) is a separate
# module invocation in the PROD account's own Terraform root, not built
# here (Phase 3a is DEV only).

data "aws_caller_identity" "current" {}

# --- OIDC provider -------------------------------------------------------

resource "aws_iam_openid_connect_provider" "github_actions" {
  url            = "https://token.actions.githubusercontent.com"
  client_id_list = ["sts.amazonaws.com"]

  # AWS verifies GitHub's OIDC endpoint against its own library of trusted
  # root CAs and only falls back to matching this thumbprint if that
  # verification is unavailable (TLS 1.3 negotiated, or the live
  # certificate cannot be fetched) -- see AWS's IAM OIDC provider
  # documentation, "Prerequisites: Validate configuration of your identity
  # provider". The value below is GitHub Actions' widely-documented,
  # currently-current root CA thumbprint; because it is a fallback rather
  # than the primary verification path for a CA AWS already trusts, an
  # eventual GitHub certificate-chain rotation does not silently break
  # authentication the way it would have before AWS's trusted-CA path
  # existed.
  thumbprint_list = ["6938fd4d98bab03faadb97b34396831e3780aea1"]
}

# --- gha-ci-dev role and its trust policy ---------------------------------

data "aws_iam_policy_document" "trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github_actions.arn]
    }

    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:aud"
      values   = ["sts.amazonaws.com"]
    }

    # StringEquals on the FULL sub claim, never StringLike with a
    # "repo:owner/repo:*" wildcard (Section 13.2) -- that wildcard would
    # let any branch, any fork's pull-request workflow, and any tag assume
    # this role. Manual workflow_dispatch on main still carries
    # `ref:refs/heads/main` in its sub claim, so the pinned condition below
    # already covers the "deliberate manual first deployment" requirement
    # without being broadened.
    condition {
      test     = "StringEquals"
      variable = "token.actions.githubusercontent.com:sub"
      values   = ["repo:${var.github_repository}:${var.github_ref}"]
    }
  }
}

resource "aws_iam_role" "gha_ci_dev" {
  name                 = "${var.resource_name_prefix}-ci-dev"
  assume_role_policy   = data.aws_iam_policy_document.trust.json
  max_session_duration = 3600
}

# --- Permissions boundary attached to every role gha-ci-dev can create ---
#
# Section 13.1/17: "Permissions boundaries are attached to all runtime
# roles the deployment identity creates, so a compromised deploy role
# cannot mint a role more privileged than the boundary allows." This is
# the ceiling; it is deliberately generous on ACTIONS (a runtime role
# still needs its own narrow policy to actually get any of these
# permissions) and narrow on RESOURCES (everything under the project
# prefix and this account/region only) -- a boundary does not grant
# anything by itself, it only caps what an attached identity policy may
# grant.
data "aws_iam_policy_document" "runtime_role_boundary" {
  statement {
    sid    = "ProjectScopedDataPlane"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:Query",
      "dynamodb:DescribeTable",
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:GenerateDataKeyWithoutPlaintext",
      "kms:DescribeKey",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:CreateLogGroup",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
      "states:StartExecution",
      "states:DescribeExecution",
      "states:StopExecution",
      "ecs:RunTask",
      "ecs:StopTask",
      "ecs:DescribeTasks",
      "lambda:InvokeFunction",
    ]
    resources = [
      "arn:aws:s3:::${var.resource_name_prefix}-*",
      "arn:aws:s3:::${var.resource_name_prefix}-*/*",
      "arn:aws:dynamodb:${var.region}:${data.aws_caller_identity.current.account_id}:table/${var.resource_name_prefix}-*",
      "arn:aws:kms:${var.region}:${data.aws_caller_identity.current.account_id}:key/*",
      "arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/4xtra/*",
      "arn:aws:ecr:${var.region}:${data.aws_caller_identity.current.account_id}:repository/${var.resource_name_prefix}-*",
      "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:stateMachine:${var.resource_name_prefix}-*",
      "arn:aws:states:${var.region}:${data.aws_caller_identity.current.account_id}:execution:${var.resource_name_prefix}-*:*",
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${var.resource_name_prefix}-*",
      "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task/${var.resource_name_prefix}-*/*",
      "arn:aws:lambda:${var.region}:${data.aws_caller_identity.current.account_id}:function:${var.resource_name_prefix}-*",
    ]
  }

  # ecr:GetAuthorizationToken does not support resource-level permissions
  # (Section 13.1's enumerated runtime exception) -- it returns only a
  # token for the caller's own account; every subsequent pull action above
  # is resource-scoped.
  statement {
    sid       = "EcrAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
}

resource "aws_iam_policy" "runtime_role_boundary" {
  name        = "${var.resource_name_prefix}-runtime-role-boundary"
  description = "Permissions boundary ceiling for every runtime role gha-ci-dev creates (Section 13.1/17)."
  policy      = data.aws_iam_policy_document.runtime_role_boundary.json
}
