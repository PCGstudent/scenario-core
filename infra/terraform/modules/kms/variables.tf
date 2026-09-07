variable "environment" {
  description = "Environment name (e.g. \"dev\"), used in the key alias and description."
  type        = string
}

variable "region" {
  description = "AWS region, used to scope the CloudWatch Logs service-principal grant to this region's log groups."
  type        = string
}

variable "key_administrators" {
  description = "ARNs (roles/users) granted full key-management (not data) permissions -- create/describe/enable/disable/schedule-deletion/tag. Populated with the deployment role; a human break-glass principal is added when it exists."
  type        = list(string)
  default     = []
}

variable "key_users" {
  description = "ARNs (roles) granted kms:Decrypt / kms:GenerateDataKey* / kms:DescribeKey -- the actual data-plane use of the key. Empty in Phase 3a (no runtime roles exist yet); populated as worker-task, ecs-execution and the Lambda roles are created in later phases (Section 13.1)."
  type        = list(string)
  default     = []
}

variable "deletion_window_in_days" {
  description = "KMS key deletion waiting period."
  type        = number
  default     = 30
}
