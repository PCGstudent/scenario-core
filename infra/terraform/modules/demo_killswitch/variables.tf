variable "environment" {
  type = string
}

variable "region" {
  type = string
}

variable "vpc_endpoint_ids" {
  type        = list(string)
  description = "The specific interface-endpoint ids to delete when the deadline fires (module.network's ecr_api/ecr_dkr/logs endpoint ids) -- never discovered by a broad tag scan, so this module's IAM grant can name exact resource ARNs instead of a wildcard."
}

variable "ecs_cluster_arns" {
  type        = list(string)
  description = "Cluster(s) to check for still-running tasks before deleting endpoints (module.worker_compute's cluster, and infra/terraform/envs/dev/probe.tf's, if both are in play during the demo window)."
}

variable "failure_alert_email" {
  type        = string
  description = "Recipient for a cleanup-failure alert -- distinct from modules/observability's operational topic, even when it is the same address, because this one specifically means \"the demo window's safety net did not work; go tear this down by hand right now.\""
}

variable "schedule_expression" {
  type        = string
  description = "A one-time EventBridge Scheduler `at(yyyy-mm-ddThh:mm:ss)` expression (UTC) -- computed by whoever stands the demo up (e.g. `timeadd(timestamp(), \"4h\")` at apply time), never a recurring cron. Deliberately a required variable with no default: this module must never silently pick its own deadline."
}
