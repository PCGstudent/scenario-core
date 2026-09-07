# Phase 3b: API Gateway HTTP API (AWS_IAM auth, Section 13.4: "no anonymous
# API path") fronting the two Lambdas Section 13.1 names {env}-api-submit
# and {env}-api-status. No API Gateway resource policy beyond IAM auth
# itself in this slice -- Section 25's recommended first vertical slice
# names only these two functions plus classify_failure (modules/job_orchestrator);
# {env}-api-registry (list model versions) is a documented, deliberate
# Phase 4+ omission, not an oversight (Section 24's own file list scopes
# Phase 3b to "API Gateway -> thin Lambda -> Step Functions -> ECS Fargate
# -> S3 + DynamoDB" against a pre-registered artifact, not model listing).

data "aws_caller_identity" "current" {}

# --- api-submit Lambda -------------------------------------------------

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

resource "aws_iam_role" "api_submit" {
  name                 = "4xtra-${var.environment}-api-submit"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "api_submit" {
  statement {
    sid       = "JobsTableReadWrite"
    effect    = "Allow"
    actions   = ["dynamodb:PutItem", "dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [var.scenario_jobs_table_arn]
  }

  statement {
    sid       = "RegistryReadOnly"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:Query"]
    resources = [var.model_registry_table_arn]
  }

  statement {
    sid       = "StartAndDescribeExecution"
    effect    = "Allow"
    actions   = ["states:StartExecution", "states:DescribeExecution"]
    resources = [var.state_machine_arn, "${replace(var.state_machine_arn, ":stateMachine:", ":execution:")}*"]
  }

  statement {
    sid       = "DataKms"
    effect    = "Allow"
    actions   = ["kms:GenerateDataKey", "kms:Decrypt"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/4xtra-${var.environment}-api-submit:*"]
  }
}

resource "aws_iam_role_policy" "api_submit" {
  name   = "api-submit"
  role   = aws_iam_role.api_submit.id
  policy = data.aws_iam_policy_document.api_submit.json
}

resource "aws_lambda_function" "api_submit" {
  function_name    = "4xtra-${var.environment}-api-submit"
  role             = aws_iam_role.api_submit.arn
  handler          = "scenario_platform.control.submit.handler"
  runtime          = "python3.13"
  timeout          = 10
  memory_size      = 256
  filename         = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)

  environment {
    variables = {
      SCENARIO_JOBS_TABLE  = var.scenario_jobs_table_name
      MODEL_REGISTRY_TABLE = var.model_registry_table_name
      STATE_MACHINE_ARN    = var.state_machine_arn
      MAX_PATH_YEARS       = tostring(var.max_path_years)
      MAX_HORIZON          = tostring(var.max_horizon)
    }
  }
}

resource "aws_cloudwatch_log_group" "api_submit" {
  name              = "/aws/lambda/4xtra-${var.environment}-api-submit"
  retention_in_days = var.log_retention_days
}

# --- api-status Lambda (GET status, GET results, DELETE cancel) -----------

resource "aws_iam_role" "api_status" {
  name                 = "4xtra-${var.environment}-api-status"
  assume_role_policy   = data.aws_iam_policy_document.lambda_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "api_status" {
  statement {
    sid       = "JobsTableRead"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem", "dynamodb:Query", "dynamodb:UpdateItem"]
    resources = [var.scenario_jobs_table_arn]
  }

  statement {
    # Section 13.1: "Presigned URLs inherit THIS role's permissions --
    # hence the narrow S3 scope." s3:GetObject only, runs/* only.
    sid       = "ReadRuns"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.runs_bucket_arn}/runs/*"]
  }

  statement {
    sid       = "StopOwnExecution"
    effect    = "Allow"
    actions   = ["states:StopExecution"]
    resources = ["${replace(var.state_machine_arn, ":stateMachine:", ":execution:")}*"]
  }

  statement {
    sid       = "DataKms"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }

  statement {
    sid       = "WriteOwnLogs"
    effect    = "Allow"
    actions   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/aws/lambda/4xtra-${var.environment}-api-status:*"]
  }
}

resource "aws_iam_role_policy" "api_status" {
  name   = "api-status"
  role   = aws_iam_role.api_status.id
  policy = data.aws_iam_policy_document.api_status.json
}

resource "aws_lambda_function" "api_status" {
  function_name    = "4xtra-${var.environment}-api-status"
  role             = aws_iam_role.api_status.arn
  handler          = "scenario_platform.control.jobs.handler"
  runtime          = "python3.13"
  timeout          = 10
  memory_size      = 256
  filename         = var.lambda_package_path
  source_code_hash = filebase64sha256(var.lambda_package_path)

  environment {
    variables = {
      SCENARIO_JOBS_TABLE = var.scenario_jobs_table_name
      RUNS_BUCKET         = var.runs_bucket_name
    }
  }
}

resource "aws_cloudwatch_log_group" "api_status" {
  name              = "/aws/lambda/4xtra-${var.environment}-api-status"
  retention_in_days = var.log_retention_days
}

# --- HTTP API, AWS_IAM auth on every route ---------------------------------

resource "aws_apigatewayv2_api" "this" {
  name          = "4xtra-${var.environment}-scenario-jobs"
  protocol_type = "HTTP"
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.this.id
  name        = "$default"
  auto_deploy = true
}

resource "aws_apigatewayv2_integration" "submit" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api_submit.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_integration" "status" {
  api_id                 = aws_apigatewayv2_api.this.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.api_status.invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "submit" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "POST /scenario-jobs"
  target             = "integrations/${aws_apigatewayv2_integration.submit.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_apigatewayv2_route" "status" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "GET /scenario-jobs/{job_id}"
  target             = "integrations/${aws_apigatewayv2_integration.status.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_apigatewayv2_route" "results" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "GET /scenario-jobs/{job_id}/results"
  target             = "integrations/${aws_apigatewayv2_integration.status.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_apigatewayv2_route" "cancel" {
  api_id             = aws_apigatewayv2_api.this.id
  route_key          = "DELETE /scenario-jobs/{job_id}"
  target             = "integrations/${aws_apigatewayv2_integration.status.id}"
  authorization_type = "AWS_IAM"
}

resource "aws_lambda_permission" "submit_invoke" {
  statement_id  = "AllowApiGatewaySubmit"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_submit.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}

resource "aws_lambda_permission" "status_invoke" {
  statement_id  = "AllowApiGatewayStatus"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.api_status.function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.this.execution_arn}/*/*"
}
