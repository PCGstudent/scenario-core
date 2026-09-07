# gha-ci-dev's own permission set -- reviewed against a DIFFERENT standard
# than runtime roles (Section 13.1 point 5): infrastructure creation
# legitimately needs a wider surface than any runtime identity, reduced
# instead by naming constraints, the permissions boundary it must attach
# to every role it creates (main.tf), and the explicit guardrail denies at
# the bottom of this file. Split into topic-scoped statements/policies for
# reviewability, not for any technical reason.
#
# Scope note: this policy covers exactly what Phase 3a's modules
# (bootstrap already applied; kms, network, artifact_store, job_store,
# worker_image, ci_oidc itself) create and manage. Phase 3b/4/5 extend it
# with additional ecs:*/states:*/lambda:*/events:*/sns:*/budgets:*
# statements as those modules are built -- by ADDING statements, never by
# loosening the naming constraints or the guardrails below.

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = var.resource_name_prefix
}

# --- 1. Terraform state I/O ------------------------------------------------

data "aws_iam_policy_document" "state_access" {
  statement {
    sid    = "StateBucketReadWrite"
    effect = "Allow"
    actions = [
      "s3:GetObject",
      "s3:PutObject",
      "s3:ListBucket",
      # S3 native locking (Terraform 1.10+, `use_lockfile = true`) writes
      # and later removes a `.tflock` object per state key -- Terraform
      # itself needs delete on that one object, not on the state object.
      "s3:DeleteObject",
    ]
    resources = [
      var.state_bucket_arn,
      "${var.state_bucket_arn}/*",
    ]
  }

  statement {
    sid    = "StateBucketKms"
    effect = "Allow"
    actions = [
      "kms:Decrypt",
      "kms:GenerateDataKey",
      "kms:DescribeKey",
    ]
    resources = [var.bootstrap_kms_key_arn]
  }
}

resource "aws_iam_role_policy" "state_access" {
  name   = "state-access"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.state_access.json
}

# --- 2. S3 / DynamoDB / KMS resource management (create + configure) ------

data "aws_iam_policy_document" "resource_management" {
  statement {
    sid    = "S3BucketLifecycleManagement"
    effect = "Allow"
    actions = [
      "s3:CreateBucket",
      "s3:PutBucketVersioning",
      "s3:PutBucketPolicy",
      "s3:GetBucketPolicy",
      "s3:DeleteBucketPolicy",
      "s3:PutEncryptionConfiguration",
      "s3:GetEncryptionConfiguration",
      "s3:PutBucketPublicAccessBlock",
      "s3:GetBucketPublicAccessBlock",
      "s3:PutLifecycleConfiguration",
      "s3:GetLifecycleConfiguration",
      "s3:PutBucketTagging",
      "s3:GetBucketTagging",
      "s3:PutBucketObjectLockConfiguration",
      "s3:GetBucketObjectLockConfiguration",
      "s3:GetBucketVersioning",
      "s3:GetBucketLocation",
      "s3:GetBucketAcl",
      "s3:PutAccountPublicAccessBlock",
      "s3:GetAccountPublicAccessBlock",
    ]
    resources = [
      "arn:aws:s3:::${local.prefix}-*",
      "arn:aws:s3:::${local.prefix}-*/*",
    ]
  }

  statement {
    sid    = "DynamoDbTableManagement"
    effect = "Allow"
    actions = [
      "dynamodb:CreateTable",
      "dynamodb:DeleteTable",
      "dynamodb:DescribeTable",
      "dynamodb:UpdateTable",
      "dynamodb:TagResource",
      "dynamodb:UntagResource",
      "dynamodb:ListTagsOfResource",
      "dynamodb:UpdateTimeToLive",
      "dynamodb:DescribeTimeToLive",
      "dynamodb:UpdateContinuousBackups",
      "dynamodb:DescribeContinuousBackups",
    ]
    resources = [
      "arn:aws:dynamodb:${var.region}:${local.account_id}:table/${local.prefix}-*",
    ]
  }

  statement {
    sid    = "KmsKeyManagement"
    effect = "Allow"
    actions = [
      "kms:DescribeKey",
      "kms:PutKeyPolicy",
      "kms:GetKeyPolicy",
      "kms:CreateAlias",
      "kms:DeleteAlias",
      "kms:UpdateAlias",
      "kms:ListAliases",
      "kms:EnableKeyRotation",
      "kms:GetKeyRotationStatus",
      "kms:TagResource",
      "kms:UntagResource",
      "kms:ScheduleKeyDeletion",
      "kms:CancelKeyDeletion",
      "kms:EnableKey",
      "kms:DisableKey",
    ]
    resources = [
      "arn:aws:kms:${var.region}:${local.account_id}:key/*",
      "arn:aws:kms:${var.region}:${local.account_id}:alias/${local.prefix}*",
    ]
  }

  statement {
    # kms:CreateKey does not support resource-level permissions (the key
    # ARN does not exist until this call returns) -- the sole enumerated
    # exception in this statement group, tracked in
    # infra/terraform/policy/resource-star-allowlist.yaml.
    sid       = "KmsCreateKey"
    effect    = "Allow"
    actions   = ["kms:CreateKey"]
    resources = ["*"]
  }

  statement {
    sid    = "EcrRepositoryManagement"
    effect = "Allow"
    actions = [
      "ecr:CreateRepository",
      "ecr:DeleteRepository",
      "ecr:DescribeRepositories",
      "ecr:PutLifecyclePolicy",
      "ecr:GetLifecyclePolicy",
      "ecr:SetRepositoryPolicy",
      "ecr:GetRepositoryPolicy",
      "ecr:DeleteRepositoryPolicy",
      "ecr:PutImageTagMutability",
      "ecr:PutImageScanningConfiguration",
      "ecr:TagResource",
      "ecr:UntagResource",
    ]
    resources = [
      "arn:aws:ecr:${var.region}:${local.account_id}:repository/${local.prefix}-*",
    ]
  }
}

resource "aws_iam_role_policy" "resource_management" {
  name   = "resource-management"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.resource_management.json
}

# --- 3. Networking (VPC, subnets, endpoints, flow logs, security groups) --
#
# EC2's VPC-family resources are the well-documented exception to
# resource-level IAM scoping: most Describe*/Create* actions for VPCs,
# subnets, route tables, security groups and VPC endpoints do not support
# resource-level permissions at all (AWS service-authorization reference),
# and a create-time condition cannot name an ARN that does not exist yet.
# Every action below is enumerated in
# infra/terraform/policy/resource-star-allowlist.yaml, deployment-role
# section, with this same justification -- never granted to any runtime
# role.
data "aws_iam_policy_document" "network_management" {
  #checkov:skip=CKV_AWS_111:EC2 VPC-family actions (VPC/subnet/route-table/security-group/endpoint create and describe) do not support resource-level IAM permissions per the AWS service authorization reference; a create-time condition cannot name an ARN that does not exist yet. Enumerated in infra/terraform/policy/resource-star-allowlist.yaml, deployment-role section. Never granted to a runtime role.
  #checkov:skip=CKV_AWS_356:Same EC2 resource-level-permission limitation as above.
  statement {
    sid    = "Ec2NetworkManagement"
    effect = "Allow"
    actions = [
      "ec2:CreateVpc",
      "ec2:DeleteVpc",
      "ec2:DescribeVpcs",
      "ec2:ModifyVpcAttribute",
      "ec2:DescribeVpcAttribute",
      "ec2:CreateSubnet",
      "ec2:DeleteSubnet",
      "ec2:DescribeSubnets",
      "ec2:ModifySubnetAttribute",
      "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable",
      "ec2:DescribeRouteTables",
      "ec2:CreateRoute",
      "ec2:DeleteRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:DescribeSecurityGroups",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:CreateVpcEndpoint",
      "ec2:DeleteVpcEndpoints",
      "ec2:DescribeVpcEndpoints",
      "ec2:ModifyVpcEndpoint",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribePrefixLists",
      "ec2:CreateFlowLogs",
      "ec2:DeleteFlowLogs",
      "ec2:DescribeFlowLogs",
      "ec2:CreateTags",
      "ec2:DeleteTags",
      "ec2:DescribeTags",
      # Explicitly NOT granted, anywhere, to any role: ec2:CreateInternetGateway,
      # ec2:AttachInternetGateway, ec2:CreateNatGateway,
      # ec2:AllocateAddress (an Elastic IP is only useful with a NAT
      # gateway or IGW here). Their absence is a second, independent
      # enforcement layer behind the "no aws_nat_gateway / aws_internet_gateway"
      # Terraform policy check (Section 17) -- even a rogue or hand-edited
      # .tf file could not apply successfully, because this role could not
      # perform the API calls such a resource would require.
    ]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "network_management" {
  name   = "network-management"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.network_management.json
}

# --- 4. Worker image push (BUILD ONCE step of deploy-dev.yml) -------------

data "aws_iam_policy_document" "image_push" {
  statement {
    sid    = "EcrPush"
    effect = "Allow"
    actions = [
      "ecr:GetDownloadUrlForLayer",
      "ecr:BatchGetImage",
      "ecr:BatchCheckLayerAvailability",
      "ecr:InitiateLayerUpload",
      "ecr:UploadLayerPart",
      "ecr:CompleteLayerUpload",
      "ecr:PutImage",
      "ecr:DescribeImages",
    ]
    resources = [var.ecr_repository_arn]
  }

  statement {
    # No resource-level support (Section 13.1's enumerated exception,
    # restated here for the deploy role specifically -- {env}-ecs-execution
    # carries the runtime-role instance of the same, separately, once
    # Phase 3b creates it).
    sid       = "EcrAuthToken"
    effect    = "Allow"
    actions   = ["ecr:GetAuthorizationToken"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "image_push" {
  name   = "image-push"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.image_push.json
}

# --- 5. IAM: create later phases' runtime roles, boundary-enforced --------

data "aws_iam_policy_document" "iam_management" {
  statement {
    sid    = "CreateProjectScopedRoles"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:DeleteRole",
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:PutRolePolicy",
      "iam:GetRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:ListPolicyVersions",
      "iam:TagPolicy",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${local.prefix}-*",
      "arn:aws:iam::${local.account_id}:policy/${local.prefix}-*",
    ]

    # A role created or reconfigured through this statement MUST carry the
    # project permissions boundary -- this is the enforcement half of
    # Section 13.1/17's "permissions boundaries are attached to all
    # runtime roles the deployment identity creates"; without this
    # condition the boundary policy in main.tf would be advisory only.
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.runtime_role_boundary.arn]
    }
  }

  statement {
    # PassRole is needed so gha-ci-dev's own `terraform apply` can attach
    # the roles it creates to the resources that assume them (e.g. an ECS
    # task definition's task/execution role, once Phase 3b creates them).
    # iam:PassedToService is required precisely so this cannot become the
    # escalation path Section 13.1 closes for the orchestrator role -- the
    # same discipline applied here, one level up the chain.
    sid    = "PassProjectScopedRoles"
    effect = "Allow"
    actions = [
      "iam:PassRole",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${local.prefix}-*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values = [
        "ecs-tasks.amazonaws.com",
        "states.amazonaws.com",
        "lambda.amazonaws.com",
      ]
    }
  }

  statement {
    sid    = "OidcProviderManagement"
    effect = "Allow"
    actions = [
      "iam:CreateOpenIDConnectProvider",
      "iam:DeleteOpenIDConnectProvider",
      "iam:GetOpenIDConnectProvider",
      "iam:UpdateOpenIDConnectProviderThumbprint",
      "iam:TagOpenIDConnectProvider",
      "iam:UntagOpenIDConnectProvider",
      "iam:ListOpenIDConnectProviders",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com",
    ]
  }
}

resource "aws_iam_role_policy" "iam_management" {
  name   = "iam-management"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.iam_management.json
}

# --- 6. Guardrails: explicit denies, independent of anything Allowed above -

data "aws_iam_policy_document" "guardrails" {
  # Section 13.1: "an explicit Deny on iam:* for principals outside the
  # project prefix." This role's own Allow statements above already only
  # name project-prefixed IAM resources; this Deny is the second,
  # independent layer that holds even if a future edit widens one of them
  # by mistake -- it cannot widen past this Deny without also removing it,
  # which is a reviewable, visible diff.
  statement {
    sid    = "DenyIamOutsideProjectPrefix"
    effect = "Deny"
    actions = [
      "iam:*",
    ]
    not_resources = [
      "arn:aws:iam::${local.account_id}:role/${local.prefix}-*",
      "arn:aws:iam::${local.account_id}:policy/${local.prefix}-*",
      "arn:aws:iam::${local.account_id}:oidc-provider/token.actions.githubusercontent.com",
    ]
  }

  # Section 13.3: "The DEV CI role carries an explicit Deny on
  # sts:AssumeRole for any ARN in the PROD account, so even a
  # misconfiguration on the PROD side cannot be exploited from DEV." No
  # statement above grants sts:AssumeRole at all (this role is CALLED via
  # AssumeRoleWithWebIdentity, it does not itself assume anything today),
  # but the Deny is written now, unconditionally, so it holds even if a
  # later phase legitimately grants this role sts:AssumeRole for some
  # same-account purpose.
  statement {
    sid       = "DenyAssumeRoleIntoProdAccount"
    effect    = "Deny"
    actions   = ["sts:AssumeRole"]
    resources = ["arn:aws:iam::${var.prod_account_id}:role/*"]
  }

  # Section 13.1: "a Deny on deleting the state bucket, the CMKs and the
  # artifacts bucket." Independent of whatever the Allow statements above
  # grant -- an explicit Deny always wins in IAM policy evaluation, so
  # this holds even against a future statement that (correctly, for other
  # buckets/keys) grants s3:DeleteBucket or kms:ScheduleKeyDeletion more
  # broadly.
  statement {
    sid    = "DenyDeletingProtectedResources"
    effect = "Deny"
    actions = [
      "s3:DeleteBucket",
      "kms:ScheduleKeyDeletion",
      "kms:DisableKey",
    ]
    resources = [
      var.state_bucket_arn,
      var.artifacts_bucket_arn,
      var.bootstrap_kms_key_arn,
      var.environment_kms_key_arn,
    ]
  }
}

resource "aws_iam_role_policy" "guardrails" {
  name   = "guardrails"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.guardrails.json
}
