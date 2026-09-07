variable "environment" {
  type        = string
  default     = "dev"
  description = "Environment name. This root configuration is DEV-specific (Section 17: envs/dev vs envs/prod are separate root configurations in separate accounts, never a workspace split)."
}

variable "region" {
  type        = string
  default     = "eu-west-1"
  description = "AWS region. The only region assumed anywhere in the design (Section 26)."
}

variable "project" {
  type    = string
  default = "4xtra"
}

variable "owner" {
  type        = string
  description = "Owner tag (Section 17: default_tags -- cost attribution depends on this being set deliberately, not left to a placeholder)."
}

variable "cost_center" {
  type    = string
  default = "4xtra-platform"
}

# --- Network (Section 14) -------------------------------------------------

variable "subnet_count" {
  type        = number
  default     = 2
  description = "Section 14.1 point 6: DEV=2 AZs, PROD=3. The only network topology lever between environments."
}

variable "flow_log_traffic_type" {
  type    = string
  default = "REJECT"
}

# --- Storage (Section 12.1) ------------------------------------------------

variable "enable_object_lock" {
  type        = bool
  default     = false
  description = "Object Lock on the artifacts bucket -- optional in DEV, on (governance mode) in PROD. Cannot be changed on an existing bucket."
}

variable "runs_expire_days" {
  type    = number
  default = 90
}

# --- ECR (Section 13.4, 20.2) ----------------------------------------------

variable "image_count_to_retain" {
  type    = number
  default = 5
}

# --- CI OIDC (Section 13.2) -------------------------------------------------

variable "github_repository" {
  type    = string
  default = "PCGstudent/scenario-core"
}

variable "github_ref" {
  type        = string
  default     = "ref:refs/heads/main"
  description = "Section 13.2: the DEV deploy role trusts pushes/dispatches against main only. A manual workflow_dispatch run on main also carries this sub claim, so the mandatory-manual-first-deployment requirement needs no separate condition."
}
