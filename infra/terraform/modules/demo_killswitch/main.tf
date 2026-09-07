# The demo window's independent auto-cleanup: a one-time EventBridge
# Scheduler firing a narrowly-scoped Lambda (lambda/cleanup.py) that stops
# any still-running demo Fargate tasks and deletes the demo's interface
# endpoints -- the dominant cost line (~$43.80/month if left standing;
# docs/aws-demo-runbook.md). Deliberately independent of GitHub Actions
# (not relied on here -- its runs are currently failing at startup) and of
# any local machine: once this apply finishes, the schedule lives entirely
# inside AWS and fires regardless of what happens to the laptop that
# created it.
#
# This module is meant to be applied IN THE SAME `terraform apply` as the
# demo network it watches, so it exists for exactly as long as the costly
# resources do -- never applied standing alone, never left behind after the
# demo's own `terraform destroy` (module.network's destroy removes the
# thing this module watches; this module's own resources are destroyed in
# the same operation as everything else scoped to the demo window).

data "aws_caller_identity" "current" {}

data "archive_file" "cleanup" {
  type        = "zip"
  source_file = "${path.module}/lambda/cleanup.py"
  output_path = "${path.module}/.build/cleanup.zip"
}

resource "aws_sns_topic" "cleanup_failure" {
  name = "4xtra-${var.environment}-demo-cleanup-failure"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.cleanup_failure.arn
  protocol  = "email"
  endpoint  = var.failure_alert_email
}

data "aws_iam_policy_document" "lambda_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "cleanup" {
  name               = "4xtra-${var.environment}-demo-cleanup"
  assume_role_policy = data.aws_iam_policy_document.lambda_trust.json
  # No permissions_boundary parameter here deliberately: this role is
  # created directly by a human running this module (the demo-deploy step),
  # the same direct-credential path bootstrap/envs-dev's first apply uses --
  # not by gha-ci-dev, whose own CreateRole grant is what requires the
  # boundary condition elsewhere in this codebase.
}

data "aws_iam_policy_document" "cleanup" {
  statement {
    sid       = "DescribeEndpoints"
    effect    = "Allow"
    actions   = ["ec2:DescribeVpcEndpoints"]
    resources = ["*"] # DescribeVpcEndpoints has no resource-level permission support.
  }

  statement {
    sid       = "DeleteOnlyTheseEndpoints"
    effect    = "Allow"
    actions   = ["ec2:DeleteVpcEndpoints"]
    resources = [for id in var.vpc_endpoint_ids : "arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:vpc-endpoint/${id}"]
  }

  statement {
    sid       = "StopTasksInDemoClustersOnly"
    effect    = "Allow"
    actions   = ["ecs:ListTasks", "ecs:StopTask"]
    resources = ["*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = var.ecs_cluster_arns
    }
  }

  statement {
    sid       = "PublishFailureAlert"
    effect    = "Allow"
    actions   = ["sns:Publish"]
    resources = [aws_sns_topic.cleanup_failure.arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/4xtra-${var.environment}-demo-cleanup:*"]
  }

  # Deliberately absent, and worth stating explicitly (per this module's own
  # brief: "must not delete results, state, or other protected resources"):
  # no s3:*, no dynamodb:*, no kms:*, no iam:*, no terraform state access of
  # any kind. This role cannot touch the artifacts bucket, the runs bucket,
  # the jobs table, or either CMK, structurally -- not by omission of a
  # policy statement that happens not to be exercised, but because no
  # statement above names any of those services at all.
}

resource "aws_iam_role_policy" "cleanup" {
  name   = "demo-cleanup"
  role   = aws_iam_role.cleanup.id
  policy = data.aws_iam_policy_document.cleanup.json
}

resource "aws_lambda_function" "cleanup" {
  function_name    = "4xtra-${var.environment}-demo-cleanup"
  role             = aws_iam_role.cleanup.arn
  handler          = "cleanup.handler"
  runtime          = "python3.13"
  timeout          = 120
  memory_size      = 256
  filename         = data.archive_file.cleanup.output_path
  source_code_hash = data.archive_file.cleanup.output_base64sha256

  environment {
    variables = {
      VPC_ENDPOINT_IDS      = join(",", var.vpc_endpoint_ids)
      ECS_CLUSTER_ARNS      = join(",", var.ecs_cluster_arns)
      FAILURE_SNS_TOPIC_ARN = aws_sns_topic.cleanup_failure.arn
    }
  }
}

resource "aws_cloudwatch_log_group" "cleanup" {
  name              = "/aws/lambda/4xtra-${var.environment}-demo-cleanup"
  retention_in_days = 14
}

# Backstop: if the Lambda fails so badly it never reaches its own
# sns.publish call (a permissions error, a timeout, an unhandled crash),
# CloudWatch's own Lambda Errors metric still fires independent of the
# function's internal logic, and this alarm still reaches the same
# recipient.
resource "aws_cloudwatch_metric_alarm" "cleanup_lambda_errors" {
  alarm_name          = "4xtra-${var.environment}-demo-cleanup-lambda-errors"
  namespace           = "AWS/Lambda"
  metric_name         = "Errors"
  dimensions          = { FunctionName = aws_lambda_function.cleanup.function_name }
  statistic           = "Sum"
  period              = 60
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "The demo auto-cleanup Lambda itself failed to run to completion -- manual teardown is required now."
  alarm_actions       = [aws_sns_topic.cleanup_failure.arn]
}

# --- One-time schedule -------------------------------------------------

resource "aws_iam_role" "scheduler_invoke" {
  name = "4xtra-${var.environment}-demo-cleanup-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "invoke-cleanup"
  role = aws_iam_role.scheduler_invoke.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.cleanup.arn
    }]
  })
}

resource "aws_scheduler_schedule" "deadline" {
  name = "4xtra-${var.environment}-demo-cleanup-deadline"

  # ONE-TIME, never recurring -- flexible_time_window OFF means it fires
  # at exactly the given instant, once, and the schedule then completes
  # (ACTION_AFTER_COMPLETION defaults to leaving the schedule in a
  # completed state rather than deleting it, which is fine: a completed
  # one-time schedule fires nothing further and costs nothing).
  schedule_expression = var.schedule_expression

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_lambda_function.cleanup.arn
    role_arn = aws_iam_role.scheduler_invoke.arn
  }
}
