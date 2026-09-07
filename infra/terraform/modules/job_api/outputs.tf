output "api_endpoint" {
  value = aws_apigatewayv2_stage.default.invoke_url
}

output "api_submit_role_arn" {
  value = aws_iam_role.api_submit.arn
}

output "api_status_role_arn" {
  value = aws_iam_role.api_status.arn
}

output "api_submit_function_name" {
  value = aws_lambda_function.api_submit.function_name
}

output "api_status_function_name" {
  value = aws_lambda_function.api_status.function_name
}
