# Two content buckets, per IMPLEMENTATION_PLAN.md Section 12.1: artifacts
# are permanent audit objects (datasets/, models/); run outputs are
# reproducible from them and therefore disposable. Account-level public
# access block is applied once, in envs/dev (not per-bucket here), to avoid
# declaring the same account-wide resource from more than one module.

data "aws_caller_identity" "current" {}

locals {
  artifacts_bucket_name = "4xtra-${var.environment}-artifacts-${data.aws_caller_identity.current.account_id}"
  runs_bucket_name      = "4xtra-${var.environment}-runs-${data.aws_caller_identity.current.account_id}"
}

# --- Artifacts bucket: datasets/, models/ -- permanent audit record -----

resource "aws_s3_bucket" "artifacts" {
  #checkov:skip=CKV_AWS_18:Object-level API activity is covered by CloudTrail S3 data events (Section 13.4/23), not a dedicated access-log bucket.
  #checkov:skip=CKV2_AWS_62:No event-driven consumer exists for artifact writes yet (Section 27: SQS/event fan-out explicitly deferred).
  #checkov:skip=CKV_AWS_144:Section 26: eu-west-1 only, no DR requirement assumed.
  bucket              = local.artifacts_bucket_name
  object_lock_enabled = var.enable_object_lock

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_versioning" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "artifacts" {
  bucket                  = aws_s3_bucket.artifacts.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# No expiration/transition rule -- Section 12.1 is explicit that this
# bucket's lifecycle is "none, audit record": datasets/ and models/ are
# permanent. This rule exists only to abort abandoned multipart uploads,
# which is hygiene, not a retention decision.
resource "aws_s3_bucket_lifecycle_configuration" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

# Object Lock configuration is a separate resource from the bucket's own
# object_lock_enabled flag (which only reserves the capability at creation
# time); this resource is the one that actually sets governance mode and a
# default retention period, and only makes sense -- and is only valid to
# apply -- when the bucket was created with object_lock_enabled = true.
resource "aws_s3_bucket_object_lock_configuration" "artifacts" {
  count  = var.enable_object_lock ? 1 : 0
  bucket = aws_s3_bucket.artifacts.id

  rule {
    default_retention {
      mode = "GOVERNANCE"
      days = var.object_lock_retention_days
    }
  }
}

resource "aws_s3_bucket_policy" "artifacts" {
  bucket = aws_s3_bucket.artifacts.id
  policy = data.aws_iam_policy_document.artifacts.json
}

data "aws_iam_policy_document" "artifacts" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.artifacts.arn,
      "${aws_s3_bucket.artifacts.arn}/*",
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
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid    = "DenyWrongKmsKey"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.artifacts.arn}/*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [var.kms_key_arn]
    }
  }
}

# --- Runs bucket: runs/{job_id}/ -- reproducible from artifacts ---------

resource "aws_s3_bucket" "runs" {
  #checkov:skip=CKV_AWS_18:Object-level API activity is covered by CloudTrail S3 data events (Section 13.4/23), not a dedicated access-log bucket.
  #checkov:skip=CKV2_AWS_62:No event-driven consumer exists for run outputs yet (Section 27: SQS/event fan-out explicitly deferred).
  #checkov:skip=CKV_AWS_144:Section 26: eu-west-1 only, no DR requirement assumed.
  #checkov:skip=CKV_AWS_21:Versioning is deliberately OFF for the runs bucket (Section 12.1) -- run outputs are reproducible from the versioned artifacts bucket, not an audit record in their own right.
  bucket = local.runs_bucket_name

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "runs" {
  bucket                  = aws_s3_bucket.runs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "runs" {
  bucket = aws_s3_bucket.runs.id

  rule {
    id     = "ia-then-expire"
    status = "Enabled"
    filter {
      prefix = "runs/"
    }
    transition {
      days          = var.runs_ia_transition_days
      storage_class = "STANDARD_IA"
    }
    expiration {
      days = var.runs_expire_days
    }
  }

  # A separate, bucket-wide rule (empty filter): abandoned multipart
  # uploads do not carry the "runs/" prefix filter above until they
  # complete, so this needs its own unscoped rule to actually catch them.
  rule {
    id     = "abort-incomplete-multipart-uploads"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "runs" {
  bucket = aws_s3_bucket.runs.id
  policy = data.aws_iam_policy_document.runs.json
}

data "aws_iam_policy_document" "runs" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.runs.arn,
      "${aws_s3_bucket.runs.arn}/*",
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
    resources = ["${aws_s3_bucket.runs.arn}/*"]
    condition {
      test     = "StringNotEquals"
      variable = "s3:x-amz-server-side-encryption"
      values   = ["aws:kms"]
    }
  }

  statement {
    sid    = "DenyWrongKmsKey"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.runs.arn}/*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "s3:x-amz-server-side-encryption-aws-kms-key-id"
      values   = [var.kms_key_arn]
    }
  }
}
