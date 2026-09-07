output "key_arn" {
  description = "ARN of the per-environment CMK. Pass into artifact_store, job_store, worker_image and the network module's Logs endpoint policy."
  value       = aws_kms_key.environment.arn
}

output "key_id" {
  value = aws_kms_key.environment.key_id
}

output "alias_name" {
  value = aws_kms_alias.environment.name
}
