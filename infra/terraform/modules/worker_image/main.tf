# The one ECR repository holding the worker image. Section 13.4: immutable
# tags, scan-on-push, a lifecycle policy, a repository policy permitting
# pulls only from the environment's execution role. `image_digest` (the
# manifest digest returned by `docker push` / `imageDigest` from
# `aws ecr describe-images`) is what deploy-dev.yml passes to Terraform as
# an input variable at deploy time -- never a mutable tag (Section 17/18.2)
# -- so this module does not itself reference any particular image; it only
# creates the repository the digest lives in.

resource "aws_ecr_repository" "worker" {
  name                 = "4xtra-${var.environment}-worker"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "KMS"
    kms_key         = var.kms_key_arn
  }
}

resource "aws_ecr_lifecycle_policy" "worker" {
  repository = aws_ecr_repository.worker.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Retain the most recent ${var.image_count_to_retain} images; expire the rest."
        selection = {
          tagStatus   = "any"
          countType   = "imageCountMoreThan"
          countNumber = var.image_count_to_retain
        }
        action = {
          type = "expire"
        }
      }
    ]
  })
}

# Only written when at least one pull principal is supplied -- see
# variables.tf. An empty repository policy document is not valid, so the
# whole resource is conditional rather than emitting a policy with no
# statements.
resource "aws_ecr_repository_policy" "worker" {
  count      = length(var.pull_principal_arns) > 0 ? 1 : 0
  repository = aws_ecr_repository.worker.name
  policy     = data.aws_iam_policy_document.pull[0].json
}

data "aws_iam_policy_document" "pull" {
  count = length(var.pull_principal_arns) > 0 ? 1 : 0

  statement {
    sid    = "AllowPullFromExecutionRole"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = var.pull_principal_arns
    }
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
    ]
  }
}
