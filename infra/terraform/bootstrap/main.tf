# Bootstrap: the state bucket every other Terraform configuration in this
# account depends on, plus the small CMK that encrypts it. This is the one
# piece of infrastructure that cannot itself live in remote state, because
# the remote state backend does not exist until this config creates it --
# see backend.tf and the README in this directory for the exact ordering.
#
# IMPLEMENTATION_PLAN.md Section 17:
#   "bootstrap/  state bucket (versioned, KMS, native locking) + the
#    bootstrap CMK; applied once with local state, then migrated"
# Section 12.1 (storage table): tfstate bucket -- versioning ON, SSE-KMS
# (CMK), noncurrent versions expire at 90 days.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
      Owner       = var.owner
      CostCenter  = var.cost_center
      Repo        = "PCGstudent/scenario-core"
      Component   = "bootstrap"
    }
  }
}

data "aws_caller_identity" "current" {}

# --- Bootstrap CMK -----------------------------------------------------
# Encrypts ONLY the Terraform state bucket below. Deliberately a separate
# key from modules/kms's per-environment CMK (Section 12.1 / 13.4): that
# CMK is created BY Terraform running against this very state bucket, so
# it cannot also be the key protecting the bucket that holds the record of
# its own creation -- a circular requirement. One small, cheap key breaks
# the cycle. It is never used for application data (S3 artifacts/runs,
# DynamoDB, ECR, CloudWatch Logs all use the environment CMK from
# modules/kms, applied once envs/dev exists).
resource "aws_kms_key" "tfstate" {
  description             = "4xtra ${var.environment} Terraform state bucket CMK (bootstrap-only; never used for application data)"
  deletion_window_in_days = 30
  enable_key_rotation     = true
  policy                  = data.aws_iam_policy_document.tfstate_key.json
}

resource "aws_kms_alias" "tfstate" {
  name          = "alias/4xtra-${var.environment}-tfstate"
  target_key_id = aws_kms_key.tfstate.key_id
}

data "aws_iam_policy_document" "tfstate_key" {
  #checkov:skip=CKV_AWS_109:Root-account "kms:*" is the standard, AWS-documented baseline every CMK needs so the key can never become unmanageable; it grants nothing to any non-root principal, and is not the runtime-role wildcard policy Section 13.1's wildcard rule is about.
  #checkov:skip=CKV_AWS_111:Same root-account KMS administrative statement as above -- required so the key is always recoverable/manageable, not a runtime write grant.
  #checkov:skip=CKV_AWS_356:Resource="*" here scopes to "this key" (a KMS key policy's Resource is implicitly the key itself; AWS does not support naming the key's own ARN in its own policy), not an account-wide wildcard.
  # Root-account admin only. Nothing else needs to touch this key directly:
  # S3 calls KMS server-side on the caller's behalf for SSE-KMS objects, so
  # the roles that read/write state (deployment roles, human operators)
  # need IAM kms:Decrypt/GenerateDataKey permission -- granted in their own
  # role policies, not by widening this key's own policy -- while the key
  # policy itself stays minimal and auditable.
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
}

# --- Terraform state bucket ---------------------------------------------
resource "aws_s3_bucket" "tfstate" {
  #checkov:skip=CKV_AWS_18:Object-level API activity on this bucket is already covered by CloudTrail S3 data events (Section 13.4/23); a dedicated access-log bucket is not part of the documented storage design (Section 12.1) and would add an unencrypted-by-default bucket with no lifecycle story of its own.
  #checkov:skip=CKV2_AWS_62:No event-driven consumer exists for state-bucket changes; nothing here would ever read the notifications.
  #checkov:skip=CKV_AWS_144:Section 26 states the design assumes eu-west-1 only with no DR requirement; cross-region replication is out of scope until that assumption changes.
  bucket = "4xtra-${var.environment}-tfstate-${data.aws_caller_identity.current.account_id}"

  # A future `terraform destroy` run against this bootstrap config must
  # never be able to casually delete the bucket every other environment's
  # state depends on. Removing this line is itself the deliberate,
  # reviewable action required before this bucket can ever be destroyed.
  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = aws_kms_key.tfstate.arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "tfstate" {
  bucket                  = aws_s3_bucket.tfstate.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  rule {
    id     = "expire-noncurrent-versions"
    status = "Enabled"
    filter {}
    noncurrent_version_expiration {
      noncurrent_days = 90
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "tfstate" {
  bucket = aws_s3_bucket.tfstate.id
  policy = data.aws_iam_policy_document.tfstate_bucket.json
}

data "aws_iam_policy_document" "tfstate_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.tfstate.arn,
      "${aws_s3_bucket.tfstate.arn}/*",
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  statement {
    sid    = "DenyUnencryptedObjectUploads"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.tfstate.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }
}
