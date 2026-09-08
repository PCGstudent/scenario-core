# Installed before network creation; discovery uses exact project endpoint Name tags.
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
  name                 = "4xtra-${var.environment}-demo-cleanup"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
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
    resources = ["arn:aws:ec2:${var.region}:${data.aws_caller_identity.current.account_id}:vpc-endpoint/*"]
    condition {
      test     = "StringEquals"
      variable = "ec2:ResourceTag/Name"
      values   = var.vpc_endpoint_names
    }
  }

  statement {
    # ecs:DescribeTasks is included here (not just ecs:ListTasks/StopTask)
    # because the tasks_stopped waiter (lambda/cleanup.py) polls
    # DescribeTasks itself to confirm a stop actually completed -- without
    # this the waiter would fail closed with AccessDenied on every call,
    # silently degrading "confirmed stopped" back to "fire and forget".
    sid       = "StopAndConfirmTasksInDemoClustersOnly"
    effect    = "Allow"
    actions   = ["ecs:ListTasks", "ecs:StopTask", "ecs:DescribeTasks"]
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
  depends_on    = [aws_iam_role_policy.cleanup, aws_cloudwatch_log_group.cleanup]
  function_name = "4xtra-${var.environment}-demo-cleanup"
  role          = aws_iam_role.cleanup.arn
  handler       = "cleanup.handler"
  runtime       = "python3.13"
  # 90s task-stop confirmation + 90s endpoint-deletion confirmation +
  # deregistration/API overhead -- 120s (an earlier version's value) was
  # too tight to let both waits run to their own documented timeouts
  # without the Lambda itself being killed first.
  timeout          = 900
  memory_size      = 256
  filename         = data.archive_file.cleanup.output_path
  source_code_hash = data.archive_file.cleanup.output_base64sha256

  environment {
    variables = {
      VPC_ENDPOINT_IDS      = join(",", var.vpc_endpoint_ids)
      ECS_CLUSTER_ARNS      = join(",", var.ecs_cluster_arns)
      VPC_ENDPOINT_NAMES    = join(",", var.vpc_endpoint_names)
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
  name                 = "4xtra-${var.environment}-demo-cleanup-scheduler"
  permissions_boundary = var.permissions_boundary_arn
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
  depends_on = [aws_iam_role_policy.scheduler_invoke, aws_cloudwatch_metric_alarm.cleanup_lambda_errors]
  name       = "4xtra-${var.environment}-demo-cleanup-deadline"

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
