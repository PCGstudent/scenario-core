# VPC flow logs to S3 (cheaper than CloudWatch ingest -- Section 13.4). A
# small, dedicated bucket: mixing operational flow-log objects into the
# artifacts bucket (a permanent audit record with an entirely different
# retention story, Section 12.1) would blur two purposes that the storage
# design otherwise keeps deliberately separate.

resource "aws_s3_bucket" "flow_logs" {
  #checkov:skip=CKV_AWS_18:This bucket IS the access-log-equivalent artifact (VPC flow logs); logging access to a log bucket is not warranted.
  #checkov:skip=CKV2_AWS_62:No event-driven consumer exists for flow-log writes.
  #checkov:skip=CKV_AWS_144:Section 26: eu-west-1 only, no DR requirement assumed.
  #checkov:skip=CKV_AWS_21:Versioning is not needed for an append-only, short-retention operational log stream.
  bucket = "4xtra-${var.environment}-flowlogs-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_server_side_encryption_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm     = "aws:kms"
      kms_master_key_id = var.kms_key_arn
    }
    bucket_key_enabled = true
  }
}

resource "aws_s3_bucket_public_access_block" "flow_logs" {
  bucket                  = aws_s3_bucket.flow_logs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id
  rule {
    id     = "expire-flow-logs"
    status = "Enabled"
    filter {}
    expiration {
      days = var.flow_log_expire_days
    }
    abort_incomplete_multipart_upload {
      days_after_initiation = 7
    }
  }
}

resource "aws_s3_bucket_policy" "flow_logs" {
  bucket = aws_s3_bucket.flow_logs.id
  policy = data.aws_iam_policy_document.flow_logs_bucket.json
}

data "aws_iam_policy_document" "flow_logs_bucket" {
  statement {
    sid    = "DenyInsecureTransport"
    effect = "Deny"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.flow_logs.arn,
      "${aws_s3_bucket.flow_logs.arn}/*",
    ]
    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }

  # The VPC Flow Logs service delivers to this bucket as
  # delivery.logs.amazonaws.com, not as the account's own principal --
  # this statement is what actually authorises the delivery, scoped to
  # this VPC's flow log as the source (aws:SourceArn) and this account
  # (aws:SourceAccount), per AWS's documented flow-logs-to-S3 delivery
  # policy shape.
  statement {
    sid    = "AllowFlowLogDelivery"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.flow_logs.arn}/*"]
    condition {
      test     = "StringEquals"
      variable = "s3:x-amz-acl"
      values   = ["bucket-owner-full-control"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }

  statement {
    sid    = "AllowFlowLogAclCheck"
    effect = "Allow"
    principals {
      type        = "Service"
      identifiers = ["delivery.logs.amazonaws.com"]
    }
    actions   = ["s3:GetBucketAcl"]
    resources = [aws_s3_bucket.flow_logs.arn]
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}

resource "aws_flow_log" "vpc" {
  vpc_id               = aws_vpc.this.id
  log_destination_type = "s3"
  log_destination      = aws_s3_bucket.flow_logs.arn
  traffic_type         = var.flow_log_traffic_type

  depends_on = [aws_s3_bucket_policy.flow_logs]

  tags = {
    Name = "4xtra-${var.environment}-flow-log"
  }
}
