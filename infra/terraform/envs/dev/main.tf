# Phase 3a: AWS foundation. Wires modules/{kms,artifact_store,job_store,
# worker_image,network,ci_oidc} together for DEV. This is everything
# Scope names for Phase 3a -- worker_compute, job_orchestrator, job_api and
# observability (Phase 3b's Files bullet) are deliberately not referenced
# here.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = var.owner
      CostCenter  = var.cost_center
      Repo        = "PCGstudent/scenario-core"
    }
  }
}

data "aws_caller_identity" "current" {}

# The bootstrap CMK's ARN, resolved through its well-known alias rather
# than a cross-state-file lookup: bootstrap (infra/terraform/bootstrap)
# keeps its own state local (see that directory's backend.tf), so this
# configuration cannot reference it as a Terraform module/remote-state
# output without adding a fragile local-state dependency. The alias name
# is deterministic (bootstrap/main.tf: "alias/4xtra-{environment}-tfstate"),
# so resolving it here, in this run's own AWS provider context, is both
# simpler and correct.
data "aws_kms_alias" "tfstate" {
  name = "alias/4xtra-${var.environment}-tfstate"
}

locals {
  # Deterministic, matching bootstrap/main.tf's naming exactly -- see
  # backend.tf's comment for why this is duplicated rather than looked up.
  state_bucket_arn = "arn:aws:s3:::4xtra-${var.environment}-tfstate-${data.aws_caller_identity.current.account_id}"

  # Duplicated from backend.tf's own `key` argument, for the same reason:
  # a `backend` block cannot reference a variable or module output, so
  # this is the one other place that value must be repeated literally.
  # Keep the two in sync by hand if the backend key ever changes.
  state_object_key = "envs/dev/terraform.tfstate"
}

# --- KMS -------------------------------------------------------------------

module "kms" {
  source = "../../modules/kms"

  environment = var.environment
  region      = var.region

  # Empty in Phase 3a: the environment CMK's key policy already contains
  # the standard "AccountRootAdmin: kms:* for root" delegation statement
  # (modules/kms), which defers authorisation to IAM for any principal in
  # this account -- gha-ci-dev's own IAM policy (modules/ci_oidc,
  # "resource-management" statement) already grants it kms:PutKeyPolicy/
  # DescribeKey/etc scoped to any key in this account by ID wildcard. Named
  # key_administrators/key_users entries are additional, explicit grants
  # for defense in depth, not required for this to function -- and adding
  # gha-ci-dev's role ARN here would create a real circular module
  # dependency (ci_oidc's own guardrails need this key's ARN). Populated
  # with runtime role ARNs (worker-task, ecs-execution, the control Lambdas)
  # as Phase 3b/4/5 create them.
  key_administrators = []
  key_users          = []
}

# --- Storage (Section 12.1) -------------------------------------------------

module "artifact_store" {
  source = "../../modules/artifact_store"

  environment        = var.environment
  kms_key_arn        = module.kms.key_arn
  enable_object_lock = var.enable_object_lock
  runs_expire_days   = var.runs_expire_days
}

module "job_store" {
  source = "../../modules/job_store"

  environment = var.environment
  kms_key_arn = module.kms.key_arn
}

module "worker_image" {
  source = "../../modules/worker_image"

  environment           = var.environment
  kms_key_arn           = module.kms.key_arn
  image_count_to_retain = var.image_count_to_retain
  # {env}-ecs-execution does not exist until Phase 3b's worker_compute
  # module creates it -- see modules/worker_image/variables.tf.
  pull_principal_arns = []
}

# --- Network (Section 14) ---------------------------------------------------

module "network" {
  source = "../../modules/network"

  environment           = var.environment
  region                = var.region
  subnet_count          = var.subnet_count
  kms_key_arn           = module.kms.key_arn
  s3_bucket_arns        = module.artifact_store.bucket_arns
  dynamodb_table_arns   = module.job_store.table_arns
  ecr_repository_arn    = module.worker_image.repository_arn
  flow_log_traffic_type = var.flow_log_traffic_type
}

# --- CI identity (Section 13.2) ---------------------------------------------

module "ci_oidc" {
  source = "../../modules/ci_oidc"

  region                  = var.region
  github_repository       = var.github_repository
  github_ref              = var.github_ref
  resource_name_prefix    = "4xtra-${var.environment}"
  state_bucket_arn        = local.state_bucket_arn
  state_object_key        = local.state_object_key
  bootstrap_kms_key_arn   = data.aws_kms_alias.tfstate.target_key_arn
  environment_kms_key_arn = module.kms.key_arn
  artifacts_bucket_arn    = module.artifact_store.artifacts_bucket_arn
  ecr_repository_arn      = module.worker_image.repository_arn
}

# --- Phase 3b: first complete vertical slice (Section 24, Section 25) ------
#
# API Gateway -> thin Lambda -> Step Functions Standard -> ECS Fargate ->
# S3 + DynamoDB, against a pre-registered frozen artifact (scripts/
# seed_registry.py -- Phase 4's calibration/promotion workflow does not
# exist yet, by design; Section 25's slice explicitly runs against a
# manually-registered artifact, not a calibrated one).
#
# Deliberately NOT wired here: infra/terraform/modules/demo_killswitch. That
# module exists to bound the standing cost of a TEMPORARY demonstration
# deployment (docs/aws-demo-runbook.md) and is applied alongside this
# environment only for the duration of such a demo -- wiring it in
# permanently here would make it a standing resource itself, which defeats
# its purpose.

module "worker_compute" {
  source = "../../modules/worker_compute"

  environment              = var.environment
  region                   = var.region
  kms_key_arn              = module.kms.key_arn
  artifacts_bucket_arn     = module.artifact_store.artifacts_bucket_arn
  artifacts_bucket_name    = module.artifact_store.artifacts_bucket_name
  runs_bucket_arn          = module.artifact_store.runs_bucket_arn
  runs_bucket_name         = module.artifact_store.runs_bucket_name
  scenario_jobs_table_arn  = module.job_store.scenario_jobs_table_arn
  scenario_jobs_table_name = module.job_store.scenario_jobs_table_name
  ecr_repository_arn       = module.worker_image.repository_arn
  ecr_repository_url       = module.worker_image.repository_url
  permissions_boundary_arn = module.ci_oidc.runtime_role_boundary_arn
}

module "job_orchestrator" {
  source = "../../modules/job_orchestrator"

  environment                = var.environment
  region                     = var.region
  cluster_arn                = module.worker_compute.cluster_arn
  task_definition_arn        = module.worker_compute.task_definition_arn
  task_definition_family_arn = "arn:aws:ecs:${var.region}:${data.aws_caller_identity.current.account_id}:task-definition/${module.worker_compute.task_definition_family}:*"
  container_name             = module.worker_compute.container_name
  worker_task_role_arn       = module.worker_compute.worker_task_role_arn
  ecs_execution_role_arn     = module.worker_compute.ecs_execution_role_arn
  subnet_ids                 = module.network.private_subnet_ids
  security_group_id          = module.network.task_security_group_id
  scenario_jobs_table_arn    = module.job_store.scenario_jobs_table_arn
  scenario_jobs_table_name   = module.job_store.scenario_jobs_table_name
  lambda_package_path        = var.control_plane_package_path
  permissions_boundary_arn   = module.ci_oidc.runtime_role_boundary_arn
}

module "job_api" {
  source = "../../modules/job_api"

  environment               = var.environment
  region                    = var.region
  scenario_jobs_table_arn   = module.job_store.scenario_jobs_table_arn
  scenario_jobs_table_name  = module.job_store.scenario_jobs_table_name
  model_registry_table_arn  = module.job_store.model_registry_table_arn
  model_registry_table_name = module.job_store.model_registry_table_name
  runs_bucket_arn           = module.artifact_store.runs_bucket_arn
  runs_bucket_name          = module.artifact_store.runs_bucket_name
  kms_key_arn               = module.kms.key_arn
  state_machine_arn         = module.job_orchestrator.state_machine_arn
  lambda_package_path       = var.control_plane_package_path
  permissions_boundary_arn  = module.ci_oidc.runtime_role_boundary_arn
}

module "observability" {
  source = "../../modules/observability"

  environment       = var.environment
  state_machine_arn = module.job_orchestrator.state_machine_arn
  alert_email       = var.alert_email
}
