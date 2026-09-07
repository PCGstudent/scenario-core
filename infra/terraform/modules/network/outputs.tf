output "vpc_id" {
  value = aws_vpc.this.id
}

output "private_subnet_ids" {
  value = aws_subnet.private[*].id
}

output "private_route_table_id" {
  value = aws_route_table.private.id
}

output "endpoints_security_group_id" {
  value = aws_security_group.endpoints.id
}

output "task_security_group_id" {
  description = "Attach future Fargate tasks (worker_compute, Phase 3b) to this security group -- created here so the network boundary is owned in exactly one module."
  value       = aws_security_group.task.id
}

output "flow_logs_bucket_name" {
  value = aws_s3_bucket.flow_logs.id
}

output "ecr_api_endpoint_id" {
  value = aws_vpc_endpoint.ecr_api.id
}

output "ecr_dkr_endpoint_id" {
  value = aws_vpc_endpoint.ecr_dkr.id
}

output "logs_endpoint_id" {
  value = aws_vpc_endpoint.logs.id
}
