output "artifacts_bucket_name" {
  value = aws_s3_bucket.artifacts.id
}

output "artifacts_bucket_arn" {
  value = aws_s3_bucket.artifacts.arn
}

output "runs_bucket_name" {
  value = aws_s3_bucket.runs.id
}

output "runs_bucket_arn" {
  value = aws_s3_bucket.runs.arn
}

output "bucket_arns" {
  description = "Both bucket ARNs, for the S3 gateway endpoint policy in the network module."
  value       = [aws_s3_bucket.artifacts.arn, aws_s3_bucket.runs.arn]
}
