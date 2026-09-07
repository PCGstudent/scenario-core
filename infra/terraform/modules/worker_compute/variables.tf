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

variable "worker_image_tag" {
  type        = string
  default     = "latest"
  description = "Mutable during early bring-up; the deploy workflow moves this to a digest pin once a real build-once/promote-by-digest pipeline exists for this module (Section 18.2, mirroring the worker image's own eventual pinning)."
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
