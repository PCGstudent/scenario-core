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

# --- Phase 3b -----------------------------------------------------------

output "api_endpoint" {
  value = module.job_api.api_endpoint
}

output "state_machine_arn" {
  value = module.job_orchestrator.state_machine_arn
}

output "worker_cluster_arn" {
  value = module.worker_compute.cluster_arn
}

output "ecr_api_endpoint_id" {
  value = module.network.ecr_api_endpoint_id
}

output "ecr_dkr_endpoint_id" {
  value = module.network.ecr_dkr_endpoint_id
}

output "logs_endpoint_id" {
  value = module.network.logs_endpoint_id
}

output "demo_killswitch_schedule_arn" {
  description = "Null unless applied with -var demo_killswitch_enabled=true. docs/aws-demo-runbook.md section 3.1: confirm this resolves immediately after any apply that was meant to create it."
  value       = try(module.demo_killswitch[0].schedule_arn, null)
}
