output "cluster_arn" {
  value = aws_ecs_cluster.worker.arn
}

output "cluster_name" {
  value = aws_ecs_cluster.worker.name
}

output "task_definition_arn" {
  value = aws_ecs_task_definition.simulate.arn
}

output "task_definition_family" {
  value = aws_ecs_task_definition.simulate.family
}

output "container_name" {
  value = "worker"
}

output "worker_task_role_arn" {
  value = aws_iam_role.worker_task.arn
}

output "ecs_execution_role_arn" {
  value = aws_iam_role.ecs_execution.arn
}

output "log_group_name" {
  value = aws_cloudwatch_log_group.worker_task.name
}
