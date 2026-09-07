output "cleanup_lambda_arn" {
  value = aws_lambda_function.cleanup.arn
}

output "schedule_arn" {
  value = aws_scheduler_schedule.deadline.arn
}

output "failure_sns_topic_arn" {
  value = aws_sns_topic.cleanup_failure.arn
}
