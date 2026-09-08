variable "environment" {
  type = string
}

variable "state_machine_arn" {
  type = string
}

variable "alert_email" {
  type        = string
  description = "Operational alert recipient (job/execution failures) -- a distinct SNS topic from the demo-cleanup killswitch's own alert (modules/demo_killswitch), even when the same address subscribes to both."
}
