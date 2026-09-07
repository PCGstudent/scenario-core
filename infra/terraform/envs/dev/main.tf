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
