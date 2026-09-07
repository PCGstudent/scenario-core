variable "environment" {
  description = "Environment name this bootstrap serves (e.g. \"dev\", \"prod\"). One bootstrap per AWS account -- DEV and PROD are separate accounts (IMPLEMENTATION_PLAN.md Section 19), never a workspace split."
  type        = string

  validation {
    condition     = contains(["dev", "prod"], var.environment)
    error_message = "environment must be \"dev\" or \"prod\"."
  }
}

variable "region" {
  description = "AWS region for the state bucket and bootstrap CMK. eu-west-1 is the only region assumed anywhere in the design (Section 26)."
  type        = string
  default     = "eu-west-1"
}

variable "project" {
  description = "Project tag applied to every resource (Section 17: default_tags)."
  type        = string
  default     = "4xtra"
}

variable "owner" {
  description = "Owner tag (Section 17: default_tags). Cost attribution depends on this being set, not left to its default."
  type        = string
  default     = "4xtra-platform-team"
}

variable "cost_center" {
  description = "CostCenter tag (Section 17: default_tags)."
  type        = string
  default     = "4xtra-platform"
}
