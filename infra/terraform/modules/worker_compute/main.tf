# Phase 3b: the ECS cluster, the "simulate" task definition and its two
# roles (architecture plan Section 6.1 step 4, Section 13.1 rows
# {env}-ecs-execution / {env}-worker-task). Fargate TASKS, launched per job
# by the state machine's ecs:runTask.sync -- never a standing ECS *service*
# (Section 24 Phase 3b: "Use Fargate tasks per job; do not introduce a
# permanent ECS service without need" -- Phase 6+'s "warm ECS service" is
# the only thing in this architecture that ever introduces idle cost, and
# it ships only against a measured trigger).
#
# Deliberately a SEPARATE cluster/roles from infra/terraform/envs/dev/probe.tf's
# probe.tf (that file's own header comment: "so they cannot collide with
# worker_compute's eventual '4xtra-dev-worker' names -- both are free to
# coexist"). This module is that "eventual" arrival.

data "aws_caller_identity" "current" {}

resource "aws_ecs_cluster" "worker" {
  #checkov:skip=CKV_AWS_65:Container Insights deferred (Section 27), same reasoning as probe.tf -- a cluster with no idle task/service costs nothing regardless.
  name = "4xtra-${var.environment}"
}

resource "aws_cloudwatch_log_group" "worker_task" {
  #checkov:skip=CKV_AWS_338:Section 15.1: 30 days DEV, 400 PROD.
  name              = "/4xtra/${var.environment}/worker"
  retention_in_days = var.log_retention_days
  kms_key_id        = var.kms_key_arn
}

# --- Execution role: pulls the worker image, ships its own container logs -

data "aws_iam_policy_document" "ecs_execution_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ecs_execution" {
  name                 = "4xtra-${var.environment}-ecs-execution"
  assume_role_policy   = data.aws_iam_policy_document.ecs_execution_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "ecs_execution" {
  statement {
    sid    = "PullWorkerImage"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [var.ecr_repository_arn]
  }

  statement {
    # Section 13.1's one enumerated runtime Resource:"*" exception,
    # restated here for this module's own copy of the grant (probe.tf
    # carries an identical, independent statement for its own role).
    sid       = "EcrAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "ShipContainerLogs"
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    resources = ["${aws_cloudwatch_log_group.worker_task.arn}:*"]
  }

  statement {
    sid       = "DecryptEcrImage"
    effect    = "Allow"
    actions   = ["kms:Decrypt"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "ecs_execution" {
  name   = "ecs-execution"
  role   = aws_iam_role.ecs_execution.id
  policy = data.aws_iam_policy_document.ecs_execution.json
}

# --- Worker task role: exactly Section 6.1 step 4's own reads/writes ------

data "aws_iam_policy_document" "worker_task_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker_task" {
  name                 = "4xtra-${var.environment}-worker-task"
  assume_role_policy   = data.aws_iam_policy_document.worker_task_trust.json
  permissions_boundary = var.permissions_boundary_arn
}

data "aws_iam_policy_document" "worker_task" {
  statement {
    # Section 6.2a: the worker reads its own job document (request +
    # resolved artifact_id) from DynamoDB; it never receives the request
    # any other way.
    sid       = "ReadOwnJob"
    effect    = "Allow"
    actions   = ["dynamodb:GetItem"]
    resources = [var.scenario_jobs_table_arn]
  }

  statement {
    # Section 13.1's disclosed honest limitation: a static policy cannot
    # scope this to the worker's OWN job id, because the job id is unknown
    # when the policy is authored. Phase 5 (session-policy narrowing via
    # sts:AssumeRole) closes this; not attempted here.
    sid       = "UpdateOwnJob"
    effect    = "Allow"
    actions   = ["dynamodb:UpdateItem"]
    resources = [var.scenario_jobs_table_arn]
  }

  statement {
    sid       = "ReadArtifacts"
    effect    = "Allow"
    actions   = ["s3:GetObject"]
    resources = ["${var.artifacts_bucket_arn}/artifacts/*"]
  }

  statement {
    sid       = "WriteRuns"
    effect    = "Allow"
    actions   = ["s3:PutObject"]
    resources = ["${var.runs_bucket_arn}/runs/*"]
  }

  statement {
    sid       = "DataKms"
    effect    = "Allow"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [var.kms_key_arn]
  }
}

resource "aws_iam_role_policy" "worker_task" {
  name   = "worker-task"
  role   = aws_iam_role.worker_task.id
  policy = data.aws_iam_policy_document.worker_task.json
}

# --- Task definition -------------------------------------------------------

resource "aws_ecs_task_definition" "simulate" {
  family                   = "4xtra-${var.environment}-simulate"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.task_cpu
  memory                   = var.task_memory
  execution_role_arn       = aws_iam_role.ecs_execution.arn
  task_role_arn            = aws_iam_role.worker_task.arn

  container_definitions = jsonencode([
    {
      name                   = "worker"
      image                  = "${var.ecr_repository_url}:${var.worker_image_tag}"
      readonlyRootFilesystem = false # the worker stages under a temp dir it creates itself
      environment = [
        { name = "SCENARIO_JOBS_TABLE", value = var.scenario_jobs_table_name },
        { name = "ARTIFACTS_BUCKET", value = var.artifacts_bucket_name },
        { name = "RUNS_BUCKET", value = var.runs_bucket_name },
        { name = "AWS_REGION", value = var.region },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.worker_task.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "simulate"
        }
      }
    }
  ])
}
