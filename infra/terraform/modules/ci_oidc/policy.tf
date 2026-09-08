# gha-ci-dev's own permission set -- reviewed against a DIFFERENT standard
# than runtime roles (Section 13.1 point 5): infrastructure creation
# legitimately needs a wider surface than any runtime identity, reduced
# instead by naming constraints, the permissions boundary it must attach
# to every role it creates (main.tf), and the explicit guardrail denies at
# the bottom of this file. Split into topic-scoped statements/policies for
# reviewability, not for any technical reason.
#
# Scope note: this policy covers the Phase 3a foundation and Phase 3b
# vertical slice. Later phases add statements rather than loosening these
# naming constraints or the guardrails below.

locals {
  account_id = data.aws_caller_identity.current.account_id
  prefix     = var.resource_name_prefix
}

# --- 1. Terraform state I/O ------------------------------------------------

data "aws_iam_policy_document" "state_access" {
  # Bucket-level listing only -- needed for Terraform's own backend
  # initialisation/state-existence checks, and carries no ability to read
  # or write any object's content.
  statement {
    sid       = "StateBucketList"
    effect    = "Allow"
    actions   = ["s3:ListBucket"]
    resources = [var.state_bucket_arn]
  }

  # The state object itself: read/write, never delete. Scoped to the
  # EXACT key this environment's backend.tf uses
  # (envs/dev/backend.tf: key = "envs/dev/terraform.tfstate") -- not a
  # `/*` wildcard across the whole bucket, since nothing else is ever
  # meant to live at another key in this environment's state bucket.
  statement {
    sid       = "StateObjectReadWrite"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject"]
    resources = ["${var.state_bucket_arn}/${var.state_object_key}"]
  }

  # The lock object only: HashiCorp's own S3-native-locking documentation
  # ("If use_lockfile is set, s3:GetObject, s3:PutObject, and
  # s3:DeleteObject are required on the lock file, e.g.
  # arn:aws:s3:::mybucket/path/to/my/key.tflock") is explicit that
  # s3:DeleteObject belongs on the LOCK file -- literally the state key
  # with ".tflock" appended -- never on the state object itself. Deleting
  # the actual state object is not a Terraform operation this role
  # performs, ever.
  statement {
    sid       = "StateLockObjectReadWriteDelete"
    effect    = "Allow"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject"]
    resources = ["${var.state_bucket_arn}/${var.state_object_key}.tflock"]
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
      # Needed for `terraform destroy` (Phase 3a acceptance criterion 1:
      # "terraform apply and terraform destroy are both clean"). The
      # guardrails statement below explicitly denies this same action on
      # the state and artifacts buckets specifically, so this broad grant
      # never actually reaches those two.
      "s3:DeleteBucket",
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
      # The read counterpart to TagResource -- KMS tag listing is its own
      # action, not implied by TagResource (unlike some services where a
      # single action covers both directions).
      "kms:ListResourceTags",
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
      "ecr:ListTagsForResource",
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
# Corrected from an earlier, inaccurate claim that EC2's VPC-family
# actions "do not support resource-level permissions at all." Verified
# directly against the AWS Service Authorization Reference and AWS's own
# guidance on tag-based EC2 access control: EC2 Create* actions in this
# family DO have documented resource types (e.g. CreateVpc -> `vpc`,
# CreateSecurityGroup -> `security-group` and `vpc`) and DO support
# resource-level ARN scoping and tag-on-create condition keys
# (`aws:RequestTag`) -- the true, narrower exception is the pure
# Describe*/List* actions, which read across an unbounded set of
# resources and genuinely have no resource-level support in EC2, the
# same as in most AWS services. The statement below is split
# accordingly: resource-type-scoped ARNs for every action that supports
# them, and a separate, honestly-labelled Resource="*" statement only for
# the Describe*/List* actions that do not. Tag-on-create condition
# enforcement (`aws:RequestTag`) is deliberately not added on top of the
# resource-type scoping in this pass -- its interaction with the
# provider's own `default_tags` merge behaviour is not something this
# repository can verify without a real `terraform apply`, and getting it
# wrong risks breaking the very deployment this PR is trying to make
# possible, which is a worse outcome than the narrower, already-real
# improvement of resource-type scoping alone.
data "aws_iam_policy_document" "network_management" {
  statement {
    sid    = "Ec2ResourceScopedManagement"
    effect = "Allow"
    actions = [
      "ec2:CreateVpc",
      "ec2:DeleteVpc",
      "ec2:ModifyVpcAttribute",
      "ec2:CreateSubnet",
      "ec2:DeleteSubnet",
      "ec2:ModifySubnetAttribute",
      "ec2:CreateRouteTable",
      "ec2:DeleteRouteTable",
      "ec2:CreateRoute",
      "ec2:DeleteRoute",
      "ec2:AssociateRouteTable",
      "ec2:DisassociateRouteTable",
      "ec2:CreateSecurityGroup",
      "ec2:DeleteSecurityGroup",
      "ec2:AuthorizeSecurityGroupIngress",
      "ec2:RevokeSecurityGroupIngress",
      "ec2:AuthorizeSecurityGroupEgress",
      "ec2:RevokeSecurityGroupEgress",
      "ec2:CreateVpcEndpoint",
      "ec2:DeleteVpcEndpoints",
      "ec2:ModifyVpcEndpoint",
      "ec2:CreateFlowLogs",
      "ec2:DeleteFlowLogs",
      "ec2:CreateTags",
      "ec2:DeleteTags",
    ]
    resources = [
      "arn:aws:ec2:${var.region}:${local.account_id}:vpc/*",
      "arn:aws:ec2:${var.region}:${local.account_id}:subnet/*",
      "arn:aws:ec2:${var.region}:${local.account_id}:route-table/*",
      "arn:aws:ec2:${var.region}:${local.account_id}:security-group/*",
      "arn:aws:ec2:${var.region}:${local.account_id}:vpc-endpoint/*",
      "arn:aws:ec2:${var.region}:${local.account_id}:vpc-flow-log/*",
    ]
  }

  # Genuinely resource-level-permission-free: pure Describe*/List*
  # actions that read across an unbounded resource set rather than
  # acting on one identified resource. This is the true, narrower
  # exception -- enumerated in
  # infra/terraform/policy/resource-star-allowlist.yaml, deployment-role
  # section. Never granted to a runtime role.
  #checkov:skip=CKV_AWS_111:Describe*/List* EC2 actions have no resource-level IAM support (they read across an unbounded resource set, not one identified resource) -- the true, narrower exception, distinct from the Create/Modify actions above which ARE now resource-type scoped. Enumerated in infra/terraform/policy/resource-star-allowlist.yaml.
  #checkov:skip=CKV_AWS_356:Same Describe/List limitation as above.
  statement {
    sid    = "Ec2DescribeOnly"
    effect = "Allow"
    actions = [
      "ec2:DescribeVpcs",
      "ec2:DescribeVpcAttribute",
      "ec2:DescribeSubnets",
      "ec2:DescribeRouteTables",
      "ec2:DescribeSecurityGroups",
      "ec2:DescribeVpcEndpoints",
      "ec2:DescribeAvailabilityZones",
      "ec2:DescribePrefixLists",
      "ec2:DescribeFlowLogs",
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

# --- 3b. Probe ECS cluster/task definition + CloudWatch Logs management --
#
# envs/dev/probe.tf's one-off connectivity-probe plumbing (Phase 3a
# acceptance criterion 3) is not itself one of the six documented Phase
# 3a modules, but gha-ci-dev still has to be able to create, refresh and
# destroy exactly what it defines: one ECS cluster, one task definition,
# one CloudWatch log group, plus the two IAM roles already covered by
# section 5 below.
data "aws_iam_policy_document" "probe_and_observability_management" {
  statement {
    sid    = "EcsClusterManagement"
    effect = "Allow"
    actions = [
      "ecs:CreateCluster",
      "ecs:DeleteCluster",
      "ecs:DescribeClusters",
      "ecs:TagResource",
      "ecs:UntagResource",
      "ecs:ListTagsForResource",
    ]
    resources = [
      "arn:aws:ecs:${var.region}:${local.account_id}:cluster/${local.prefix}-*",
      "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${local.prefix}-*",
    ]
  }

  # Verified per-action against the AWS Service Authorization Reference
  # for ECS (list_ecs.html), not assumed to share one blanket limitation:
  # RegisterTaskDefinition's own row lists resource type
  # "task-definition*" (required) -- it DOES support resource-level
  # scoping, corrected from an earlier revision that lumped it in with
  # the three genuinely Resource="*"-only actions below on the strength
  # of a tracked containers-roadmap issue that turns out to describe
  # those three, not this one.
  statement {
    sid       = "EcsTaskDefinitionRegistration"
    effect    = "Allow"
    actions   = ["ecs:RegisterTaskDefinition"]
    resources = ["arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${local.prefix}-*"]
  }

  statement {
    # Confirmed individually against the same AWS Service Authorization
    # Reference page: DeregisterTaskDefinition, DescribeTaskDefinition and
    # ListTaskDefinitions each list no resource type at all (the
    # "Resource types (*required)" column is blank) -- Resource must be
    # "*" for exactly these three. A genuinely different limitation from
    # EC2's (corrected in network_management above) and from
    # RegisterTaskDefinition's own row just above: verified per action,
    # not assumed to travel together. Enumerated in
    # infra/terraform/policy/resource-star-allowlist.yaml.
    sid    = "EcsTaskDefinitionReadOnlyAndDeregister"
    effect = "Allow"
    actions = [
      "ecs:DeregisterTaskDefinition",
      "ecs:DescribeTaskDefinition",
      "ecs:ListTaskDefinitions",
    ]
    resources = ["*"]
  }

  statement {
    sid    = "ProbeLogGroupManagement"
    effect = "Allow"
    actions = [
      "logs:CreateLogGroup",
      "logs:DeleteLogGroup",
      "logs:PutRetentionPolicy",
      "logs:AssociateKmsKey",
      # Both the pre- and post-migration CloudWatch Logs tagging action
      # names are granted here since this repository has not run a real
      # `terraform apply` to observe which one the pinned provider
      # version (~> 6.63) actually calls -- granting both is the accurate
      # response to that uncertainty, not a guess dressed up as either.
      # Each is individually confirmed against the AWS Service
      # Authorization Reference (list_logs.html) to list "log-group" as a
      # supported resource type.
      "logs:TagResource",
      "logs:UntagResource",
      "logs:ListTagsForResource",
      "logs:TagLogGroup",
      "logs:UntagLogGroup",
      "logs:ListTagsLogGroup",
    ]
    resources = [
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/4xtra/*",
      "arn:aws:logs:${var.region}:${local.account_id}:log-group:/aws/lambda/${local.prefix}-*",
    ]
  }

  statement {
    # Confirmed against the AWS Service Authorization Reference for
    # CloudWatch Logs (list_logs.html): DescribeLogGroups' own row lists
    # no resource type at all (the "Resource types (*required)" column is
    # blank) -- it reads across every log group in the account, not one
    # identified resource, and genuinely requires Resource="*". Every
    # other action in "ProbeLogGroupManagement" above lists "log-group" as
    # a supported resource type and stays scoped there; this is the one,
    # individually-verified exception, not an assumption carried over
    # from it. Enumerated in
    # infra/terraform/policy/resource-star-allowlist.yaml.
    sid       = "ProbeLogGroupDescribe"
    effect    = "Allow"
    actions   = ["logs:DescribeLogGroups"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "probe_and_observability_management" {
  name   = "probe-and-observability-management"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.probe_and_observability_management.json
}

# --- 3c. Phase 3b control-plane/orchestration resource management ---------

data "aws_iam_policy_document" "phase3b_management" {
  statement {
    sid    = "LambdaManagement"
    effect = "Allow"
    actions = [
      "lambda:CreateFunction", "lambda:DeleteFunction", "lambda:GetFunction",
      "lambda:GetFunctionCodeSigningConfig", "lambda:GetPolicy",
      "lambda:UpdateFunctionCode", "lambda:UpdateFunctionConfiguration",
      "lambda:AddPermission", "lambda:RemovePermission",
      "lambda:TagResource", "lambda:UntagResource", "lambda:ListTags",
    ]
    resources = ["arn:aws:lambda:${var.region}:${local.account_id}:function:${local.prefix}-*"]
  }

  statement {
    sid    = "StepFunctionsManagement"
    effect = "Allow"
    actions = [
      "states:CreateStateMachine", "states:UpdateStateMachine",
      "states:DeleteStateMachine", "states:DescribeStateMachine",
      "states:TagResource", "states:UntagResource", "states:ListTagsForResource",
    ]
    resources = ["arn:aws:states:${var.region}:${local.account_id}:stateMachine:${local.prefix}-*"]
  }

  statement {
    sid    = "HttpApiManagement"
    effect = "Allow"
    actions = ["apigateway:GET", "apigateway:POST", "apigateway:PATCH", "apigateway:DELETE"]
    resources = [
      "arn:aws:apigateway:${var.region}::/apis",
      "arn:aws:apigateway:${var.region}::/apis/*",
      "arn:aws:apigateway:${var.region}::/tags/*",
    ]
  }

  statement {
    sid    = "SchedulerManagement"
    effect = "Allow"
    actions = [
      "scheduler:CreateSchedule", "scheduler:UpdateSchedule",
      "scheduler:DeleteSchedule", "scheduler:GetSchedule",
      "scheduler:TagResource", "scheduler:UntagResource", "scheduler:ListTagsForResource",
    ]
    resources = ["arn:aws:scheduler:${var.region}:${local.account_id}:schedule/default/${local.prefix}-*"]
  }

  statement {
    sid    = "SnsTopicManagement"
    effect = "Allow"
    actions = [
      "sns:CreateTopic", "sns:DeleteTopic", "sns:GetTopicAttributes",
      "sns:SetTopicAttributes", "sns:Subscribe", "sns:ListSubscriptionsByTopic",
      "sns:TagResource", "sns:UntagResource", "sns:ListTagsForResource",
    ]
    resources = ["arn:aws:sns:${var.region}:${local.account_id}:${local.prefix}-*"]
  }

  statement {
    # Unsubscribe acts on an opaque subscription ARN returned only after
    # confirmation; AWS does not support constraining it to a topic ARN.
    sid       = "SnsSubscriptionDelete"
    effect    = "Allow"
    actions   = ["sns:Unsubscribe"]
    resources = ["*"]
  }

  statement {
    sid    = "CloudWatchAlarmManagement"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricAlarm", "cloudwatch:DeleteAlarms",
      "cloudwatch:DescribeAlarms", "cloudwatch:TagResource",
      "cloudwatch:UntagResource", "cloudwatch:ListTagsForResource",
    ]
    resources = ["arn:aws:cloudwatch:${var.region}:${local.account_id}:alarm:${local.prefix}-*"]
  }
}

resource "aws_iam_role_policy" "phase3b_management" {
  name   = "phase3b-management"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.phase3b_management.json
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
  # Boundary-conditioned. Verified against AWS's own IAM User Guide
  # ("Permissions boundaries for IAM entities", the worked
  # `CreateOrChangeOnlyWithBoundary` example): the `iam:PermissionsBoundary`
  # condition key checks "the specified policy is attached as permissions
  # boundary on the IAM principal resource" -- for a role that does not
  # yet carry that boundary (or carries none at all), the key is absent
  # from the request context and `StringEquals` on a missing key evaluates
  # false, so the statement simply does not match. AWS's own example
  # bundles exactly this action set -- Create*/PutPolicy/Attach/Detach/
  # DeletePolicy -- under one such condition; Get/Update/Delete/List/Tag
  # actions are deliberately NOT included here (see the next statement)
  # because AWS's own example puts those in a separate, unconditioned
  # statement instead, and there is no basis to assume they carry this
  # context key reliably.
  #
  # gha-ci-dev's own role is explicitly excluded below (guardrails'
  # "DenySelfPolicyModification"), belt-and-suspenders on top of the
  # structural fact that gha-ci-dev itself carries no boundary and so
  # could never satisfy this condition against itself anyway.
  statement {
    sid    = "CreateOrModifyRolePoliciesOnlyWithBoundary"
    effect = "Allow"
    actions = [
      "iam:CreateRole",
      "iam:PutRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:DeleteRolePolicy",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${local.prefix}-*",
    ]
    condition {
      test     = "StringEquals"
      variable = "iam:PermissionsBoundary"
      values   = [aws_iam_policy.runtime_role_boundary.arn]
    }
  }

  # Unconditioned: reads and role lifecycle that do not carry (or cannot
  # be relied on to carry) the iam:PermissionsBoundary context key,
  # matching where AWS's own worked example places their user-resource
  # equivalents (GetRolePolicy, ListRolePolicies, UpdateUser, DeleteUser
  # all sit in that example's separate, unconditioned statement).
  # iam:DeleteRole is included here rather than with the boundary-gated
  # actions above because AWS's example places DeleteUser here too;
  # gha-ci-dev deleting its OWN role is separately, explicitly denied
  # below regardless of this Allow's resource match.
  statement {
    sid    = "RoleReadAndLifecycle"
    effect = "Allow"
    actions = [
      "iam:GetRole",
      "iam:UpdateRole",
      "iam:DeleteRole",
      "iam:TagRole",
      "iam:UntagRole",
      "iam:GetRolePolicy",
      "iam:ListRolePolicies",
      "iam:ListAttachedRolePolicies",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:role/${local.prefix}-*",
    ]
  }

  # Policy OBJECTS (not role-boundary attachment) -- the
  # iam:PermissionsBoundary condition key is about what is attached to a
  # PRINCIPAL, not about a managed policy resource itself, so it has no
  # bearing on these actions at all.
  statement {
    sid    = "PolicyObjectManagement"
    effect = "Allow"
    actions = [
      "iam:CreatePolicy",
      "iam:DeletePolicy",
      "iam:GetPolicy",
      "iam:GetPolicyVersion",
      "iam:CreatePolicyVersion",
      "iam:DeletePolicyVersion",
      "iam:ListPolicyVersions",
      "iam:TagPolicy",
      "iam:UntagPolicy",
    ]
    resources = [
      "arn:aws:iam::${local.account_id}:policy/${local.prefix}-*",
    ]
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

  # Closes the self-escalation path a compromised (or carelessly edited)
  # gha-ci-dev could otherwise use even though every Allow statement
  # above is scoped to project-prefixed resources: gha-ci-dev's own ARN
  # DOES match that prefix, and iam_management's boundary-conditioned
  # statement structurally cannot match against it (gha-ci-dev carries no
  # boundary of its own -- see that statement's comment) -- but this Deny
  # makes that guarantee explicit and independent of that reasoning, not
  # reliant on it alone. If gha-ci-dev's own trust policy or inline
  # policies ever need to change, that is exactly the kind of change the
  # bootstrap/README.md ordering already reserves for a human running
  # Terraform with direct account credentials, never for CI acting on
  # itself.
  statement {
    sid    = "DenySelfPolicyModification"
    effect = "Deny"
    actions = [
      "iam:PutRolePolicy",
      "iam:AttachRolePolicy",
      "iam:DetachRolePolicy",
      "iam:DeleteRolePolicy",
      "iam:DeleteRole",
      "iam:UpdateAssumeRolePolicy",
    ]
    resources = [aws_iam_role.gha_ci_dev.arn]
  }

  # No statement anywhere grants iam:PutRolePermissionsBoundary or
  # iam:DeleteRolePermissionsBoundary, so a boundary set at iam:CreateRole
  # time is already, structurally, permanent for as long as the role
  # exists (only delete-and-recreate can change it, which re-runs the
  # same boundary-conditioned CreateRole check). This Deny makes that
  # explicit and unconditional rather than leaving it as an absence that
  # a future edit could silently fill in.
  statement {
    sid    = "DenyBoundaryReplacementOrRemoval"
    effect = "Deny"
    actions = [
      "iam:PutRolePermissionsBoundary",
      "iam:DeleteRolePermissionsBoundary",
    ]
    resources = ["*"]
  }

  # AWS's own IAM User Guide worked example ("Permissions boundaries for
  # IAM entities", the Maria/Zhang delegation walkthrough) includes
  # exactly this guardrail under the name "NoBoundaryPolicyEdit": denying
  # CreatePolicyVersion/DeletePolicy/DeletePolicyVersion/
  # SetDefaultPolicyVersion on the boundary policy itself. Without it,
  # gha-ci-dev's own "PolicyObjectManagement" statement above (scoped to
  # every project-prefixed policy, including the boundary policy, since
  # it also carries the project prefix) would let it rewrite the
  # boundary's content to be maximally permissive -- defeating the
  # boundary requirement on iam:CreateRole entirely without ever touching
  # a Put/DeleteRolePermissionsBoundary call.
  statement {
    sid    = "DenyBoundaryPolicyEdit"
    effect = "Deny"
    actions = [
      "iam:CreatePolicyVersion",
      "iam:DeletePolicy",
      "iam:DeletePolicyVersion",
      "iam:SetDefaultPolicyVersion",
    ]
    resources = [aws_iam_policy.runtime_role_boundary.arn]
  }
}

resource "aws_iam_role_policy" "guardrails" {
  name   = "guardrails"
  role   = aws_iam_role.gha_ci_dev.id
  policy = data.aws_iam_policy_document.guardrails.json
}
