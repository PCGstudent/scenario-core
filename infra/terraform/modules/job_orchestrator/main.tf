# Phase 3b: the scenario Step Functions Standard workflow (architecture plan
# Section 6.1 step 3, Section 6.3) and its ClassifyFailure Lambda (Section
# 13.1 row {env}-classify-failure). No declarative Retry block anywhere in
# state_machine.asl.json.tftpl -- Section 6.3 explains at length why one
# cannot correctly express this classification, and
# tests/infra/test_state_machine_definition.py asserts the shape structurally.

data "aws_caller_identity" "current" {}

# --- ClassifyFailure Lambda -------------------------------------------------

data "aws_iam_policy_document" "classify_failure_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "classify_failure" {
  name                 = "4xtra-${var.environment}-classify-failure"
  assume_role_policy   = data.aws_iam_policy_document.classify_failure_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "classify_failure" {
  statement {
    sid       = "DescribeOwnClusterTasks"
    effect    = "Allow"
    actions   = ["ecs:DescribeTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.cluster_arn]
    }
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/4xtra-${var.environment}-classify-failure:*"]
  }
}

resource "aws_iam_role_policy" "classify_failure" {
  name   = "classify-failure"
  role   = aws_iam_role.classify_failure.id
  policy = data.aws_iam_policy_document.classify_failure.json
}

resource "aws_lambda_function" "classify_failure" {
  function_name    = "4xtra-${var.environment}-classify-failure"
  role             = aws_iam_role.classify_failure.arn
  handler          = "scenario_platform.control.classify_failure.handler"
  runtime          = "python3.13"
  timeout          = 30
  memory_size      = 256
  filename         = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)
}

resource "aws_cloudwatch_log_group" "classify_failure" {
  name              = "/aws/lambda/4xtra-${var.environment}-classify-failure"
  retention_in_days = var.log_retention_days
}

# --- Step Functions state machine + its role --------------------------------

data "aws_iam_policy_document" "sfn_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "sfn_orchestrator" {
  name                 = "4xtra-${var.environment}-sfn-orchestrator"
  assume_role_policy   = data.aws_iam_policy_document.sfn_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "sfn_orchestrator" {
  statement {
    sid       = "RunSimulationTask"
    effect    = "Allow"
    actions   = ["ecs:RunTask"]
    resources = [var.task_definition_family_arn]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.cluster_arn]
    }
  }

  statement {
    sid       = "ManageOwnTasks"
    effect    = "Allow"
    actions   = ["ecs:StopTask", "ecs:DescribeTasks"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [var.cluster_arn]
    }
  }

  statement {
    # Section 13.1: "PassRole -- the escalation path, closed explicitly."
    # Named exactly the two task role ARNs, conditioned on the one service
    # they may be passed to.
    sid       = "PassTaskRoles"
    effect    = "Allow"
    actions   = ["iam:PassRole"]
    resources = [var.worker_task_role_arn, var.ecs_execution_role_arn]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    # The .sync integration's own managed EventBridge rule (Section 13.1:
    # "resource-scopable... and is scoped").
    sid       = "SyncCallbackRule"
    effect    = "Allow"
    actions   = ["events:PutRule", "events:PutTargets", "events:DescribeRule"]
    resources = ["arn:aws:events:${var.region}:${data.aws_caller_identity.current.account_id}:rule/StepFunctionsGetEventsForECSTaskRule"]
  }

  statement {
    sid       = "RecordJobStatus"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.scenario_jobs_table_arn]
  }

  statement {
    sid       = "InvokeClassifyFailureOnly"
    effect    = "Allow"
    actions   = ["lambda:InvokeFunction"]
    resources = [aws_lambda_function.classify_failure.arn]
  }

  statement {
    # Execution-history logging -- AWS's cross-account log-delivery API
    # family (CreateLogDelivery et al., the exact statement AWS's own
    # Step Functions logging setup documentation requires) does not
    # support resource-level permissions at all (allowlisted in
    # infra/terraform/policy/resource-star-allowlist.yaml).
    sid    = "StateMachineLogDelivery"
    effect = "Allow"
    actions = [
      "logs:CreateLogDelivery",
      "logs:GetLogDelivery",
      "logs:UpdateLogDelivery",
      "logs:DeleteLogDelivery",
      "logs:ListLogDeliveries",
      "logs:PutResourcePolicy",
      "logs:DescribeResourcePolicies",
    ]
    resources = ["*"]
  }

  statement {
    # Verified individually against the AWS Service Authorization
    # Reference for CloudWatch Logs (list_logs.html), exactly like
    # modules/ci_oidc's own "ProbeLogGroupDescribe" statement:
    # DescribeLogGroups' own row lists no resource type at all, and it is
    # never bundled with a resource-scoped action in the same statement.
    sid       = "SfnLogGroupDescribe"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "sfn_orchestrator" {
  name   = "sfn-orchestrator"
  role   = aws_iam_role.sfn_orchestrator.id
  policy = data.aws_iam_policy_document.sfn_orchestrator.json
}

resource "aws_cloudwatch_log_group" "sfn" {
  name              = "/4xtra/${var.environment}/scenario-job-executions"
  retention_in_days = var.log_retention_days
}

resource "aws_sfn_state_machine" "scenario_job" {
  name     = "4xtra-${var.environment}-scenario-job"
  role_arn = aws_iam_role.sfn_orchestrator.arn
  type     = "STANDARD"

  definition = templatefile("${path.module}/state_machine.asl.json.tftpl", {
    jobs_table_name               = var.scenario_jobs_table_name
    cluster_arn                   = var.cluster_arn
    task_definition_arn           = var.task_definition_arn
    container_name                = var.container_name
    subnet_ids_json               = jsonencode(var.subnet_ids)
    security_group_id             = var.security_group_id
    classify_failure_function_arn = aws_lambda_function.classify_failure.arn
    task_timeout_seconds          = var.task_timeout_seconds
    max_attempts                  = var.max_attempts
    retry_wait_seconds            = var.retry_wait_seconds
  })

  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn.arn}:*"
    include_execution_data = true
    level                  = "ERROR"
  }
}
