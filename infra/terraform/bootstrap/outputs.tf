output "state_bucket_name" {
  description = "Name of the Terraform remote state bucket. Feed this into envs/{env}/backend.tf's bucket argument."
  value       = aws_s3_bucket.tfstate.id
}

output "state_bucket_arn" {
  value = aws_s3_bucket.tfstate.arn
}

output "tfstate_kms_key_arn" {
  description = "ARN of the bootstrap-only CMK encrypting the state bucket. NOT the per-environment CMK used by application resources -- see modules/kms."
  value       = aws_kms_key.tfstate.arn
}

output "tfstate_kms_alias" {
  value = aws_kms_alias.tfstate.name
}
