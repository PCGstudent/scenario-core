# AWS creates a permissive default security group for every VPC
# automatically, outside Terraform's control unless explicitly managed --
# this locks it down to allow nothing at all and ensures nothing is ever
# attached to it by omitting an explicit security_group_ids argument
# elsewhere.
resource "aws_default_security_group" "this" {
  vpc_id = aws_vpc.this.id
  # No ingress, no egress blocks: an empty rule set on a managed default
  # security group means AWS removes every rule it shipped with.

  tags = {
    Name = "4xtra-${var.environment}-default-locked-down"
  }
}

# Two security groups (Section 14.1 point 1, Section 14.3): one for the
# interface-endpoint ENIs, one for the future Fargate task ENIs (created
# now -- worker_compute in Phase 3b attaches tasks to this SG rather than
# creating its own, so the network boundary is owned in exactly one place).

resource "aws_security_group" "endpoints" {
  name        = "4xtra-${var.environment}-endpoints"
  description = "Interface VPC endpoints (ecr.api, ecr.dkr, logs) -- accepts HTTPS from the task security group only."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "4xtra-${var.environment}-endpoints"
  }
}

resource "aws_vpc_security_group_ingress_rule" "endpoints_from_task" {
  security_group_id            = aws_security_group.endpoints.id
  description                  = "HTTPS from the task security group."
  referenced_security_group_id = aws_security_group.task.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}

# Interface-endpoint ENIs are the server side of the connection; they never
# initiate outbound traffic of their own, so no egress rule is granted.

resource "aws_security_group" "task" {
  #checkov:skip=CKV2_AWS_5:Intentionally created ahead of the compute that will use it -- Section 14.1/17 places security-group ownership in the network module specifically so the network boundary is defined in exactly one place; worker_compute (Phase 3b) attaches Fargate tasks to this SG rather than creating its own.
  name        = "4xtra-${var.environment}-task"
  description = "Future Fargate tasks (Section 14.1 point 1): no ingress at all -- nothing initiates a connection to a task. Egress limited to HTTPS toward the endpoint security group only."
  vpc_id      = aws_vpc.this.id

  tags = {
    Name = "4xtra-${var.environment}-task"
  }
}

# Deliberately NO ingress rule of any kind on this security group -- Section
# 14.1 point 1: "a security group with no inbound rules at all."

resource "aws_vpc_security_group_egress_rule" "task_to_endpoints" {
  security_group_id            = aws_security_group.task.id
  description                  = "HTTPS to the interface-endpoint security group only -- the sole egress path (Section 14.3)."
  referenced_security_group_id = aws_security_group.endpoints.id
  from_port                    = 443
  to_port                      = 443
  ip_protocol                  = "tcp"
}
