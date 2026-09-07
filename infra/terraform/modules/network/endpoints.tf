# Gateway endpoints (S3, DynamoDB): free, route-table-based, no ENI and no
# security group of their own -- reachability is a route to the service's
# prefix list, so the task security group needs an explicit egress rule to
# that prefix list (added below), separate from the endpoints SG used by
# the interface endpoints.
#
# Interface endpoints (ecr.api, ecr.dkr, logs): exactly three, per Section
# 14.1 point 4 -- no more, no fewer. STS, KMS, Step Functions and Secrets
# Manager are deliberately NOT provisioned; the reasoning for each is
# recorded in Section 14.1 and not repeated in code comments here.

# --- S3 gateway endpoint --------------------------------------------------

resource "aws_vpc_endpoint" "s3" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.s3"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  policy            = data.aws_iam_policy_document.s3_endpoint.json

  tags = {
    Name = "4xtra-${var.environment}-s3"
  }
}

data "aws_iam_policy_document" "s3_endpoint" {
  # Restricted to the two project buckets (Section 14.1 point 3): an
  # arbitrary third-party or cross-account bucket is unreachable from the
  # network layer through this endpoint even with over-broad IAM.
  statement {
    sid    = "AllowProjectBucketsOnly"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions = ["s3:*"]
    resources = concat(
      var.s3_bucket_arns,
      [for arn in var.s3_bucket_arns : "${arn}/*"],
    )
  }

  # ECR stores every image layer in an AWS-managed S3 bucket outside this
  # account, and layer downloads flow over this same S3 gateway endpoint
  # (docker.io/AWS: "Amazon ECR uses Amazon S3 to store your image
  # layers... they must access Amazon ECR to get the image manifest and
  # then Amazon S3 to download the actual image layers"). Without this
  # statement, ECR manifest calls (over the ecr.api/ecr.dkr interface
  # endpoints) succeed but every layer pull fails -- the endpoint policy
  # would otherwise reject it before IAM is even consulted. Verified
  # directly against AWS's official ECR VPC-endpoints documentation
  # ("Minimum Amazon S3 Bucket Permissions for Amazon ECR"), which
  # specifies exactly this action and exactly this ARN pattern -- no
  # object-level prefix narrower than "/*" is documented as sufficient,
  # and no other action (e.g. s3:ListBucket) is required or granted here.
  # This is AWS's own managed bucket, never a third-party or
  # customer-controlled one; it is the only resource outside the project
  # prefix this endpoint policy ever names.
  statement {
    sid    = "AllowEcrLayerBucketReadOnly"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["s3:GetObject"]
    resources = ["arn:aws:s3:::prod-${var.region}-starport-layer-bucket/*"]
  }
}

resource "aws_vpc_security_group_egress_rule" "task_to_s3_gateway" {
  security_group_id = aws_security_group.task.id
  description       = "HTTPS to the S3 gateway endpoint prefix list."
  prefix_list_id    = aws_vpc_endpoint.s3.prefix_list_id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# --- DynamoDB gateway endpoint ---------------------------------------------

resource "aws_vpc_endpoint" "dynamodb" {
  vpc_id            = aws_vpc.this.id
  service_name      = "com.amazonaws.${var.region}.dynamodb"
  vpc_endpoint_type = "Gateway"
  route_table_ids   = [aws_route_table.private.id]
  policy            = data.aws_iam_policy_document.dynamodb_endpoint.json

  tags = {
    Name = "4xtra-${var.environment}-dynamodb"
  }
}

data "aws_iam_policy_document" "dynamodb_endpoint" {
  statement {
    sid    = "AllowProjectTablesOnly"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["dynamodb:*"]
    resources = var.dynamodb_table_arns
  }
}

resource "aws_vpc_security_group_egress_rule" "task_to_dynamodb_gateway" {
  security_group_id = aws_security_group.task.id
  description       = "HTTPS to the DynamoDB gateway endpoint prefix list."
  prefix_list_id    = aws_vpc_endpoint.dynamodb.prefix_list_id
  from_port         = 443
  to_port           = 443
  ip_protocol       = "tcp"
}

# --- ECR API interface endpoint (auth token, image manifest) -------------

resource "aws_vpc_endpoint" "ecr_api" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.ecr.api"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  policy              = data.aws_iam_policy_document.ecr_endpoint.json

  tags = {
    Name = "4xtra-${var.environment}-ecr-api"
  }
}

# --- ECR DKR interface endpoint (docker registry protocol -- layer pulls) -

resource "aws_vpc_endpoint" "ecr_dkr" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.ecr.dkr"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  policy              = data.aws_iam_policy_document.ecr_endpoint.json

  tags = {
    Name = "4xtra-${var.environment}-ecr-dkr"
  }
}

data "aws_iam_policy_document" "ecr_endpoint" {
  # Restricted to the one repository (Section 14.1 point 3). GetAuthorizationToken
  # is not resource-scopable (it names no repository), so it is allowed for
  # any resource at the endpoint-policy layer -- the IAM layer (Section
  # 13.1) is what actually restricts who may call it and what they can do
  # with the returned token.
  statement {
    sid    = "AllowAuth"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }

  statement {
    sid    = "AllowThisRepositoryOnly"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["ecr:*"]
    resources = [var.ecr_repository_arn]
  }
}

# --- CloudWatch Logs interface endpoint (awslogs driver) -----------------

resource "aws_vpc_endpoint" "logs" {
  vpc_id              = aws_vpc.this.id
  service_name        = "com.amazonaws.${var.region}.logs"
  vpc_endpoint_type   = "Interface"
  subnet_ids          = aws_subnet.private[*].id
  security_group_ids  = [aws_security_group.endpoints.id]
  private_dns_enabled = true
  policy              = data.aws_iam_policy_document.logs_endpoint.json

  tags = {
    Name = "4xtra-${var.environment}-logs"
  }
}

data "aws_iam_policy_document" "logs_endpoint" {
  # No log group exists yet in Phase 3a (worker_compute, Phase 3b, creates
  # the first one) -- restricted to this account's own principals rather
  # than a specific log-group ARN, which is the standard tightening for an
  # endpoint policy when the resource does not exist yet. Revisit to name
  # the exact log-group ARN once Phase 3b creates it, if a narrower policy
  # is judged worth the added coupling between modules.
  statement {
    sid    = "AllowThisAccountOnly"
    effect = "Allow"
    principals {
      type        = "*"
      identifiers = ["*"]
    }
    actions   = ["logs:*"]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "aws:PrincipalAccount"
      values   = [data.aws_caller_identity.current.account_id]
    }
  }
}
