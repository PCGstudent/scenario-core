# The one CMK per environment, covering S3 (artifacts, runs), DynamoDB
# (jobs, registry), ECR and CloudWatch Logs (IMPLEMENTATION_PLAN.md Section
# 13.4). Deliberately NOT the bootstrap CMK that encrypts the Terraform
# state bucket (infra/terraform/bootstrap) -- that key is created before
# this module can exist and is never widened to cover application data.

data "aws_caller_identity" "current" {}

resource "aws_kms_key" "environment" {
  description             = "4xtra ${var.environment} environment CMK -- S3 artifacts/runs, DynamoDB, ECR, CloudWatch Logs"
  deletion_window_in_days = var.deletion_window_in_days
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.key_policy.json
}

resource "aws_kms_alias" "environment" {
  name          = "alias/4xtra-${var.environment}"
  target_key_id = aws_kms_key.environment.key_id
}

data "aws_iam_policy_document" "key_policy" {
  #checkov:skip=CKV_AWS_109:Root-account "kms:*" is the standard AWS-documented baseline every CMK needs so the key can never become unmanageable; it is not a runtime-role grant.
  #checkov:skip=CKV_AWS_111:Same root-account administrative statement as above.
  #checkov:skip=CKV_AWS_356:Resource="*" in a KMS key policy scopes to "this key" -- AWS key policies do not support naming the key's own ARN as a resource.
  statement {
    sid    = "AccountRootAdmin"
    effect = "Allow"
    principals {
      type        = "AWS"
      identifiers = ["arn:aws:iam::${data.aws_caller_identity.current.account_id}:root"]
    }
    actions   = ["kms:*"]
    resources = ["*"]
  }

  # Key *management* (not data use): create/describe/enable/tag/schedule
  # deletion. Deliberately excludes kms:Decrypt / kms:GenerateDataKey* --
  # an administrator manages the key, a user uses it, and the two lists
  # are not the same principals (Section 13.1's least-privilege posture
  # applied to the key itself, not just to S3/DynamoDB/ECR).
  dynamic "statement" {
    for_each = length(var.key_administrators) > 0 ? [1] : []
    content {
      sid    = "KeyAdministrators"
      effect = "Allow"
      principals {
        type        = "AWS"
        identifiers = var.key_administrators
      }
      actions = [
        "kms:Create*",
        "kms:Describe*",
        "kms:Enable*",
        "kms:List*",
        "kms:Put*",
        "kms:Update*",
        "kms:Revoke*",
        "kms:Disable*",
        "kms:Get*",
        "kms:Delete*",
        "kms:TagResource",
        "kms:UntagResource",
        "kms:ScheduleKeyDeletion",
        "kms:CancelKeyDeletion",
      ]
      resources = ["*"]
    }
  }

  dynamic "statement" {
    for_each = length(var.key_users) > 0 ? [1] : []
    content {
      sid    = "KeyUsers"
      effect = "Allow"
      principals {
        type        = "AWS"
        identifiers = var.key_users
      }
      actions = [
        "kms:Decrypt",
        "kms:GenerateDataKey",
        "kms:GenerateDataKeyWithoutPlaintext",
        "kms:DescribeKey",
      ]
      resources = ["*"]
    }
  }

  # CloudWatch Logs requires an explicit key-policy grant to the regional
  # service principal before a log group can be created with this CMK --
  # an IAM permission on the caller alone is not sufficient for this one
  # service, unlike S3/DynamoDB where SSE-KMS is authorised purely through
  # the caller's IAM permissions. Scoped to log groups in this account and
  # region via the encryption-context condition AWS documents for this
  # exact grant.
  statement {
    sid    = "AllowCloudWatchLogs"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["logs.${var.region}.amazonaws.com"]
    }
    actions = [
      "kms:Encrypt*",
      "kms:Decrypt*",
      "kms:ReEncrypt*",
      "kms:GenerateDataKey*",
      "kms:Describe*",
    ]
    resources = ["*"]
    condition {
      test     = "ArnLike"
      variable = "kms:EncryptionContext:aws:logs:arn"
      values   = ["arn:aws:logs:${var.region}:${data.aws_caller_identity.current.account_id}:log-group:*"]
    }
  }
}
