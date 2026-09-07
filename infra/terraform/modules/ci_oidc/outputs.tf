output "oidc_provider_arn" {
  value = aws_iam_openid_connect_provider.github_actions.arn
}

output "gha_ci_dev_role_arn" {
  description = "Role ARN deploy-dev.yml's `aws-actions/configure-aws-credentials` assumes over OIDC."
  value       = aws_iam_role.gha_ci_dev.arn
}

output "runtime_role_boundary_arn" {
  description = "Permissions boundary ARN that must be attached to every runtime role created in later phases."
  value       = aws_iam_policy.runtime_role_boundary.arn
}
