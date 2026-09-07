variable "region" {
  type        = string
  description = "AWS region."
}

variable "github_repository" {
  type        = string
  description = "GitHub \"owner/repo\" this role trusts. Section 13.2: the OIDC trust condition is StringEquals on the FULL sub claim, never StringLike with a wildcard."
  default     = "PCGstudent/scenario-core"
}

variable "github_ref" {
  type        = string
  description = "The exact ref (Section 13.2's sub claim suffix) this role's trust policy is pinned to, e.g. \"ref:refs/heads/main\" for the DEV deploy role. PROD's equivalent (not created by this module invocation) pins \"environment:prod\" instead."
  default     = "ref:refs/heads/main"
}

variable "resource_name_prefix" {
  type        = string
  description = "Naming-constraint prefix (Section 13.1: \"resource ARNs restricted to the 4xtra-{env}-* prefix wherever the action supports it\"). Also the prefix the explicit iam:* deny below exempts."
  default     = "4xtra-dev"
}

variable "state_bucket_arn" {
  type        = string
  description = "ARN of the Terraform state bucket (bootstrap output) -- read/write access, plus the deny-delete protection."
}

variable "bootstrap_kms_key_arn" {
  type        = string
  description = "ARN of the bootstrap CMK that encrypts the state bucket (bootstrap output) -- decrypt/encrypt access for state I/O, plus the deny-delete protection."
}

variable "environment_kms_key_arn" {
  type        = string
  description = "ARN of the per-environment CMK (modules/kms output) -- deploy-time manage access, plus the deny-delete protection."
}

variable "artifacts_bucket_arn" {
  type        = string
  description = "ARN of the artifacts bucket (modules/artifact_store output) -- the deny-delete protection (Section 13.1: \"a Deny on deleting the state bucket, the CMKs and the artifacts bucket\")."
}

variable "ecr_repository_arn" {
  type        = string
  description = "ARN of the worker ECR repository (modules/worker_image output) -- push access."
}

variable "prod_account_id" {
  type        = string
  description = "PROD AWS account id. Used only in the explicit Deny on cross-account sts:AssumeRole (Section 13.3) -- this role is never granted any Allow naming a PROD ARN."
  default     = "488182246436"
}
