# Section 17: "Outputs: API endpoint, bucket names, table names, ECR
# repository URL, state machine ARNs, cluster ARN, CI role ARNs. Nothing
# sensitive." (API endpoint / state machine ARN / cluster ARN arrive in
# Phase 3b.)

output "artifacts_bucket_name" {
  value = module.artifact_store.artifacts_bucket_name
}

output "runs_bucket_name" {
  value = module.artifact_store.runs_bucket_name
}

output "scenario_jobs_table_name" {
  value = module.job_store.scenario_jobs_table_name
}

output "model_registry_table_name" {
  value = module.job_store.model_registry_table_name
}

output "ecr_repository_url" {
  value = module.worker_image.repository_url
}

output "vpc_id" {
  value = module.network.vpc_id
}

output "private_subnet_ids" {
  value = module.network.private_subnet_ids
}

output "task_security_group_id" {
  value = module.network.task_security_group_id
}

output "environment_kms_key_arn" {
  value = module.kms.key_arn
}

output "gha_ci_dev_role_arn" {
  description = "Configure this as the role-to-assume in deploy-dev.yml's aws-actions/configure-aws-credentials step."
  value       = module.ci_oidc.gha_ci_dev_role_arn
}

output "runtime_role_boundary_arn" {
  value = module.ci_oidc.runtime_role_boundary_arn
}
