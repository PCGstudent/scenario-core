output "scenario_jobs_table_name" {
  value = aws_dynamodb_table.scenario_jobs.name
}

output "scenario_jobs_table_arn" {
  value = aws_dynamodb_table.scenario_jobs.arn
}

output "model_registry_table_name" {
  value = aws_dynamodb_table.model_registry.name
}

output "model_registry_table_arn" {
  value = aws_dynamodb_table.model_registry.arn
}

output "table_arns" {
  description = "Both table ARNs, for the DynamoDB gateway endpoint policy in the network module."
  value       = [aws_dynamodb_table.scenario_jobs.arn, aws_dynamodb_table.model_registry.arn]
}
