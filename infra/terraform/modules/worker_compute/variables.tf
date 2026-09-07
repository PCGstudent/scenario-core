variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "kms_key_arn" {
  type        = string
  description = "Environment CMK (modules/kms) -- decrypt/generate-data-key for the worker task role, and encrypts this module's own log group."
}

variable "artifacts_bucket_arn" {
  type = string
}

variable "runs_bucket_arn" {
  type = string
}

variable "scenario_jobs_table_arn" {
  type = string
}

variable "scenario_jobs_table_name" {
  type = string
}

variable "artifacts_bucket_name" {
  type = string
}

variable "runs_bucket_name" {
  type = string
}

variable "ecr_repository_arn" {
  type = string
}

variable "ecr_repository_url" {
  type = string
}

variable "worker_image_digest" {
  type        = string
  description = "The worker image's immutable content digest (\"sha256:...\", from `aws ecr describe-images` after the manual build+push handoff in docs/architecture/IMPLEMENTATION_PLAN.md Section 18.2's build-once/promote-by-digest pipeline) -- referenced as `{repository_url}@{digest}`, never `{repository_url}:{tag}`. No default: a task definition that silently floats to whatever a mutable tag currently resolves to is exactly what promote-by-digest exists to prevent (the same reasoning Section 18.3 applies to the PROD image-copy job), and the manifest's own `worker_image_ref` provenance field (worker/__main__.py, from WORKER_IMAGE_REF) is only a meaningful audit trail if this value is pinned, not floating."
}

variable "permissions_boundary_arn" {
  type        = string
  description = "modules/ci_oidc output -- required on every role this module's deploy identity creates (Section 13.1 point 6)."
}

variable "task_cpu" {
  type        = string
  default     = "512"
  description = "0.5 vCPU (Section 11.3's DEV default)."
}

variable "task_memory" {
  type        = string
  default     = "1024"
  description = "1 GB (Section 11.3's DEV default)."
}

variable "log_retention_days" {
  type    = number
  default = 30
}
