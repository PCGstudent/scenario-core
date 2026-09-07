output "repository_name" {
  value = aws_ecr_repository.worker.name
}

output "repository_arn" {
  description = "ARN of the ECR repository. Feed into the network module's ecr.api/ecr.dkr endpoint policies to restrict pulls to this one repository (Section 14.1)."
  value       = aws_ecr_repository.worker.arn
}

output "repository_url" {
  description = "Repository URL for `docker push`/`docker pull` (deploy-dev.yml's BUILD ONCE step)."
  value       = aws_ecr_repository.worker.repository_url
}
