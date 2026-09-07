# Two tables, zero global secondary indexes (IMPLEMENTATION_PLAN.md Section
# 12.3: "Result: zero global secondary indexes in the first functional
# platform" -- every access pattern in the table there is a single-item
# GetItem/UpdateItem or a Query against the table's own primary key, never
# an index). Deferred-GSI attributes (status_shard, requested_by, cache_key)
# are written by the application from day one but are NOT declared here --
# DynamoDB only requires declaring attributes that participate in a key
# schema, and these do not yet.

resource "aws_dynamodb_table" "scenario_jobs" {
  #checkov:skip=CKV_AWS_28:No GSIs by design (Section 12.3) -- point-in-time recovery and encryption below are the relevant protections; this check flags the absence of a backup plan, which PITR (enabled below) already provides.
  name         = "4xtra-${var.environment}-scenario-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  # JOB#{job_id} holds the resolved request and status (Section 6.2a: "the
  # request lives in the job item", not a second write). IDEM#{principal}#
  # {key} items share the same table and hash key namespace (Section 6.2:
  # a GSI would be eventually consistent and therefore *incorrect* for
  # idempotency here) and expire via TTL below.
  attribute {
    name = "pk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}

resource "aws_dynamodb_table" "model_registry" {
  #checkov:skip=CKV_AWS_28:No GSIs by design (Section 12.3); PITR (enabled below) is the relevant protection.
  name         = "4xtra-${var.environment}-model-registry"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  # Disjoint partition-key NAMESPACES (CANDIDATE#, APPROVAL#, POINTER#,
  # INDEX#, RETIREMENT#) are what let dynamodb:LeadingKeys IAM conditions
  # enforce different write permissions per record class (Section 8.4 /
  # 13.1) -- the calibrator role can write CANDIDATE#/INDEX# but not
  # APPROVAL#/POINTER#/RETIREMENT#, and the model-approver role is the
  # exact reverse. That enforcement lives in the roles that write here
  # (created in Phase 4), not in this table's schema.
  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled     = true
    kms_key_arn = var.kms_key_arn
  }
}
