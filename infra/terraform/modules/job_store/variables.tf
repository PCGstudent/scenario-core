variable "environment" {
  type        = string
  description = "Environment name, used in table naming (4xtra-{env}-{table})."
}

variable "kms_key_arn" {
  type        = string
  description = "ARN of the per-environment CMK (modules/kms) used for SSE on both tables."
}
