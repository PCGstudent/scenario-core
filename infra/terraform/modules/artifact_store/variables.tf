variable "environment" {
  type        = string
  description = "Environment name, used in bucket naming (4xtra-{env}-{component}-{account})."
}

variable "kms_key_arn" {
  type        = string
  description = "ARN of the per-environment CMK (modules/kms) used for SSE-KMS on both buckets."
}

variable "enable_object_lock" {
  type        = bool
  default     = false
  description = "Object Lock on the artifacts bucket. Section 12.1: governance mode + retention in PROD, optional in DEV. MUST be set at bucket creation -- Object Lock cannot be enabled on an existing bucket, so flipping this later means a new bucket, not an in-place change."
  nullable    = false
}

variable "object_lock_retention_days" {
  type        = number
  default     = 400
  description = "Governance-mode retention period in days, used only when enable_object_lock is true."
  nullable    = false
}

variable "runs_ia_transition_days" {
  type        = number
  default     = 30
  description = "Days before a runs/ object transitions to Standard-IA (Section 12.1: 30 d in both environments)."
  nullable    = false
}

variable "runs_expire_days" {
  type        = number
  description = "Days before a runs/ object expires. Section 12.1: 90 d DEV, 400 d PROD."
  nullable    = false
}
