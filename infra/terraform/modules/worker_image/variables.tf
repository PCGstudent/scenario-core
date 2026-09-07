variable "environment" {
  type        = string
  description = "Environment name, used in repository naming (4xtra-{env}-worker)."
}

variable "kms_key_arn" {
  type        = string
  description = "ARN of the per-environment CMK (modules/kms) used for repository encryption."
}

variable "image_count_to_retain" {
  type        = number
  description = "ECR lifecycle policy: number of images to retain. Section 20.2: 5 in DEV, 20 in PROD."
  default     = 5
  nullable    = false
}

variable "pull_principal_arns" {
  type        = list(string)
  description = <<-EOT
    ARNs allowed to pull, via an explicit repository policy statement.
    Section 13.4: "a repository policy permitting pulls only from the
    environment's execution role." Empty by design in Phase 3a: the
    execution role ({env}-ecs-execution) does not exist until Phase 3b's
    worker_compute module creates it. An empty list here means no
    resource-policy restriction is added yet -- same-account IAM-permission
    -based pulls (if any principal is ever granted ecr:* IAM permission)
    still work; the resource-policy layer of restriction is added the
    moment Phase 3b supplies the execution role's ARN, with no change
    needed here beyond passing a non-empty list.
  EOT
  default     = []
}
