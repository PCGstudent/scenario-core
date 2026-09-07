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

# --- Phase 3b: vertical slice (Section 24, 25) ------------------------------

variable "worker_image_digest" {
  type        = string
  description = "See modules/worker_compute's identical variable -- the worker image's immutable content digest (\"sha256:...\"), never a mutable tag. No default: passed explicitly at apply time from `aws ecr describe-images` after the manual build+push handoff."
}

variable "control_plane_package_path" {
  type        = string
  description = "Path to the built control-plane Lambda zip (scripts/package_control_plane.py). No default -- deploy-dev.yml (extended, Phase 3b's own Files bullet) builds this artifact and passes its path explicitly; nothing here builds it, the same way the worker image is built and pushed outside Terraform entirely."
}

variable "alert_email" {
  type        = string
  description = "Operational-alert recipient for modules/observability's SNS topic (Step Functions execution failures). A separate concern from modules/demo_killswitch's own failure alert, even when the same address is used for both."
}

variable "demo_killswitch_enabled" {
  type        = bool
  default     = false
  description = "Off by default -- creates modules/demo_killswitch (the demo-window auto-cleanup) in the SAME apply as module.network's costly endpoints, so both are created atomically rather than in a second, separate apply that would leave the endpoints unwatched in between (docs/aws-demo-runbook.md section 3.1). Only ever set true for the duration of a demo window."
}

variable "demo_schedule_expression" {
  type        = string
  default     = null
  description = "Required (non-null) only when demo_killswitch_enabled=true: a one-time EventBridge Scheduler `at(yyyy-mm-ddThh:mm:ss)` expression (UTC), e.g. computed via `timeadd(timestamp(), \"4h\")` at apply time. See modules/demo_killswitch's own identical variable for why this has no default of its own."
}
