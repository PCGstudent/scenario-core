variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "scenario_jobs_table_arn" {
  type = string
}

variable "scenario_jobs_table_name" {
  type = string
}

variable "model_registry_table_arn" {
  type = string
}

variable "model_registry_table_name" {
  type = string
}

variable "runs_bucket_arn" {
  type = string
}

variable "runs_bucket_name" {
  type = string
}

variable "kms_key_arn" {
  type = string
}

variable "state_machine_arn" {
  type = string
}

variable "lambda_package_path" {
  type        = string
  description = "See modules/job_orchestrator's identical variable -- the same built zip serves all three control-plane Lambdas (submit, jobs, classify_failure); only the handler path differs per function."
}

variable "permissions_boundary_arn" {
  type = string
}

variable "max_path_years" {
  type        = number
  default     = 300000
  description = "See scenario_platform.control.admission's identical constant/docstring for the derivation -- keep both in sync."
}

variable "max_horizon" {
  type    = number
  default = 2520
}

variable "log_retention_days" {
  type    = number
  default = 30
}


variable "demo_deadline_utc" {
  type        = string
  default     = null
  description = "UTC RFC3339 cutoff, enforced by IAM before each new execution/placement."
}
