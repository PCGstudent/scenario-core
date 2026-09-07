# Phase 3b observability: one SNS topic and one alarm on the state
# machine's own AWS/States ExecutionsFailed metric (Section 6.3's table row
# "Step Functions execution itself fails -> Alarm; the job record sits at
# its last recorded state and is reconciled by an operator").
#
# Deliberately NOT built here: an OrphanedSubmissions alarm (Section 6.2a
# names the condition explicitly but assigns its detection to a Phase 6
# reconciler that does not exist yet -- "Until that reconciler exists, the
# condition is alarmed on and handled manually" describes an operator
# noticing it by hand, not a metric this module can honestly claim to
# publish); and per-model-metric alarms (persistence, tail index -- Section
# 24 Phase 5's own Files bullet, PROD hardening, not this slice).

resource "aws_sns_topic" "operational_alerts" {
  name = "4xtra-${var.environment}-operational-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  topic_arn = aws_sns_topic.operational_alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

resource "aws_cloudwatch_metric_alarm" "executions_failed" {
  alarm_name          = "4xtra-${var.environment}-scenario-job-executions-failed"
  namespace           = "AWS/States"
  metric_name         = "ExecutionsFailed"
  dimensions          = { StateMachineArn = var.state_machine_arn }
  statistic           = "Sum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 0
  comparison_operator = "GreaterThanThreshold"
  treat_missing_data  = "notBreaching"
  alarm_description   = "One or more scenario-job Step Functions executions failed (Section 6.3's own escalation for an execution-level failure, as opposed to a classified task failure already recorded on the job item)."
  alarm_actions       = [aws_sns_topic.operational_alerts.arn]
}
