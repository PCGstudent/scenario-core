output "state_machine_arn" {
  value = aws_sfn_state_machine.scenario_job.arn
}

output "sfn_orchestrator_role_arn" {
  value = aws_iam_role.sfn_orchestrator.arn
}

output "classify_failure_function_arn" {
  value = aws_lambda_function.classify_failure.arn
}

output "classify_failure_function_name" {
  value = aws_lambda_function.classify_failure.function_name
}
