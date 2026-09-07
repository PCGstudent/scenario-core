# Phase 3a acceptance criterion 3: "The connectivity probe proves
# S3/DynamoDB/ECR/Logs are reachable and the internet is not."
# scripts/connectivity_probe.py is the actual check; this file is the
# minimal, one-off ECS Fargate plumbing to run it inside the private
# subnet, attached to the `task` security group modules/network already
# created.
#
# Deliberately NOT a reusable module, and deliberately separate from
# Phase 3b's worker_compute (Files bullet, Section 24): this is
# acceptance tooling for Phase 3a's own network, not the application. Its
# cluster/role/task-definition names are prefixed "probe" specifically so
# they cannot collide with worker_compute's eventual "4xtra-dev-worker"
# names -- both are free to coexist, and this one is cheap to leave in
# place indefinitely (an ECS cluster and a registered task definition
# cost nothing at rest; only `ecs:RunTask` invocations do, and this is
# invoked on demand, never as a standing service).
#
# Fargate task startup and the 1-5 second checks themselves cost well
# under a cent per run; nothing here is a recurring cost line in the
# deployment summary.

resource "aws_ecs_cluster" "probe" {
  #checkov:skip=CKV_AWS_65:Container Insights is explicitly deferred (Section 27): "billed per observed metric, for data the worker emits for free via EMF; revisit when per-task system metrics become genuinely necessary in PROD." Not warranted for a one-off acceptance-check cluster.
  name = "4xtra-${var.environment}-probe"
}

resource "aws_cloudwatch_log_group" "probe_task" {
  #checkov:skip=CKV_AWS_338:Section 15.1: 30 days in DEV, 400 in PROD -- this is DEV acceptance tooling, not a PROD data plane.
  # The task's own container stdout (the JSON report line), via the
  # awslogs driver -- distinct from the log group the probe SCRIPT itself
  # creates as part of its logs-reachability check
  # (PROBE_LOG_GROUP, /4xtra/connectivity-probe by default).
  name              = "/4xtra/${var.environment}/probe"
  retention_in_days = 30
  kms_key_id        = module.kms.key_arn
}

# --- Execution role: pulls the probe image, ships its own container logs -

data "aws_iam_policy_document" "probe_execution_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "probe_execution" {
  name               = "4xtra-${var.environment}-probe-execution"
  assume_role_policy = data.aws_iam_policy_document.probe_execution_trust.json
  # Required for gha-ci-dev to be able to create this role at all: its own
  # iam:CreateRole grant (modules/ci_oidc) is conditioned on the caller
  # requesting exactly this boundary. Omitting this argument would mean
  # the CreateRole API call carries no PermissionsBoundary parameter, the
  # iam:PermissionsBoundary condition key would be absent from the
  # request, and gha-ci-dev's boundary-conditioned statement would not
  # match -- creation would fail closed with AccessDenied, not silently
  # succeed unboundaried.
  permissions_boundary = module.ci_oidc.runtime_role_boundary_arn
}

data "aws_iam_policy_document" "probe_execution" {
  statement {
    sid    = "PullProbeImage"
    effect = "Allow"
    actions = [
      "ecr:BatchGetImage",
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchCheckLayerAvailability",
    ]
    resources = [module.worker_image.repository_arn]
  }

  statement {
    # Section 13.1's enumerated runtime exception, this role's own
    # instance of it -- restated in infra/terraform/policy/resource-star-allowlist.yaml.
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
    resources = ["${aws_cloudwatch_log_group.probe_task.arn}:*"]
  }
}

resource "aws_iam_role_policy" "probe_execution" {
  name   = "probe-execution"
  role   = aws_iam_role.probe_execution.id
  policy = data.aws_iam_policy_document.probe_execution.json
}

# --- Task role: exactly what connectivity_probe.py's checks call --------

data "aws_iam_policy_document" "probe_task_trust" {
  statement {
    effect  = "Allow"
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "probe_task" {
  name               = "4xtra-${var.environment}-probe-task"
  assume_role_policy = data.aws_iam_policy_document.probe_task_trust.json
  # See probe_execution's identical comment above.
  permissions_boundary = module.ci_oidc.runtime_role_boundary_arn
}

data "aws_iam_policy_document" "probe_task" {
  statement {
    # HeadBucket (check_s3) is authorised by s3:ListBucket on the bucket,
    # not s3:GetObject -- the probe never reads/writes an object.
    sid       = "S3Reachability"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [module.artifact_store.artifacts_bucket_arn, module.artifact_store.runs_bucket_arn]
  }

  statement {
    sid    = "DynamoDbReachability"
    effect = "Allow"
    actions = [
      "dynamodb:DescribeTable",
    ]
    resources = [
      module.job_store.scenario_jobs_table_arn,
      module.job_store.model_registry_table_arn,
    ]
  }

  statement {
    sid    = "EcrReachability"
    effect = "Allow"
    actions = [
      "ecr:DescribeRepositories",
    ]
    resources = [module.worker_image.repository_arn]
  }

  statement {
    sid       = "EcrAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "LogsReachability"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:CreateLogStream",
      "logs:PutLogEvents",
    ]
    # The probe creates its OWN check log group at runtime
    # (PROBE_LOG_GROUP) -- scoped to the /4xtra/ prefix this project uses
    # for every log group, not to one that must already exist.
    resources = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:/4xtra/*"]
  }
}

resource "aws_iam_role_policy" "probe_task" {
  name   = "probe-task"
  role   = aws_iam_role.probe_task.id
  policy = data.aws_iam_policy_document.probe_task.json
}

# --- Task definition ------------------------------------------------------

resource "aws_ecs_task_definition" "probe" {
  family                   = "4xtra-${var.environment}-connectivity-probe"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = "256"
  memory                   = "512"
  execution_role_arn       = aws_iam_role.probe_execution.arn
  task_role_arn            = aws_iam_role.probe_task.arn

  container_definitions = jsonencode([
    {
      name = "connectivity-probe"
      # Pushed once, manually, as part of the deployment handoff sequence
      # (docs/architecture/adr and the PR's deployment summary): docker
      # build -f docker/probe.Dockerfile -t <repo_url>:probe . && docker
      # push <repo_url>:probe. Never rebuilt by deploy-dev.yml -- this is
      # acceptance tooling, not the application's build-once/promote-by-
      # digest artifact (Section 18.2).
      image                  = "${module.worker_image.repository_url}:probe"
      readonlyRootFilesystem = true
      environment = [
        { name = "PROBE_ARTIFACTS_BUCKET", value = module.artifact_store.artifacts_bucket_name },
        { name = "PROBE_RUNS_BUCKET", value = module.artifact_store.runs_bucket_name },
        { name = "PROBE_SCENARIO_JOBS_TABLE", value = module.job_store.scenario_jobs_table_name },
        { name = "PROBE_MODEL_REGISTRY_TABLE", value = module.job_store.model_registry_table_name },
        { name = "PROBE_ECR_REPOSITORY", value = module.worker_image.repository_name },
      ]
      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.probe_task.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = "probe"
        }
      }
    }
  ])
}
