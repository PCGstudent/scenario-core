variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "cluster_arn" {
  type = string
}

variable "task_definition_arn" {
  type = string
}

variable "task_definition_family_arn" {
  type        = string
  description = "The task-definition FAMILY ARN (no revision suffix), what ecs:RunTask's IAM condition is scoped against -- distinct from task_definition_arn, which names one specific revision."
}

variable "container_name" {
  type = string
}

variable "worker_task_role_arn" {
  type = string
}

variable "ecs_execution_role_arn" {
  type = string
}

variable "subnet_ids" {
  type = list(string)
}

variable "security_group_id" {
  type = string
}

variable "scenario_jobs_table_arn" {
  type = string
}

variable "scenario_jobs_table_name" {
  type = string
}

variable "lambda_package_path" {
  type        = string
  description = "Path to the built control-plane Lambda zip (scripts/package_control_plane.py's output). No default -- deploy-dev.yml builds this artifact and passes its path explicitly; there is deliberately nothing for Terraform itself to build here (mirrors the worker image's own manual build+push handoff)."
}

variable "permissions_boundary_arn" {
  type = string
}

variable "task_timeout_seconds" {
  type        = number
  default     = 600
  description = "Static stand-in for Section 6.1's fully admission-derived, per-request timeout. A dynamic value needs a DynamoDB read of the job's own timeout_s before RunSimulation (the execution input is deliberately just {job_id}, Section 6.2a); not built in this slice. 600s comfortably covers admission.MAX_PATH_YEARS's worst case at the plan's own ~1.39ms/path-year figure with a wide safety margin."
}

variable "max_attempts" {
  type        = number
  default     = 2
  description = "Section 6.3: \"attempt < MAX_ATTEMPTS (2)\"."
}

variable "retry_wait_seconds" {
  type    = number
  default = 10
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
