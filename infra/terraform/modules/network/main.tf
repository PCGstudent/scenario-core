# Private subnets only. NO aws_internet_gateway, NO aws_nat_gateway --
# anywhere in this module, structurally, not just by omission (Section 14.1
# point 5, Section 27). tests/test_terraform_policy.py enforces this by
# scanning every .tf file in infra/terraform for either resource type.
#
# Identical topology in every environment; var.subnet_count is the only
# lever (Section 14.1 point 6/7) -- DEV=2 AZs, PROD=3.

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

resource "aws_vpc" "this" {
  cidr_block           = var.vpc_cidr
  enable_dns_support   = true
  enable_dns_hostnames = true

  tags = {
    Name = "4xtra-${var.environment}"
  }
}

resource "aws_subnet" "private" {
  count             = var.subnet_count
  vpc_id            = aws_vpc.this.id
  availability_zone = data.aws_availability_zones.available.names[count.index]
  # A /20 per subnet (4,096 addresses) out of the /16 VPC -- Fargate ENIs
  # are the only consumer and there is no address-space pressure at this
  # scale; sized generously once rather than revisited per phase.
  cidr_block              = cidrsubnet(var.vpc_cidr, 4, count.index)
  map_public_ip_on_launch = false

  tags = {
    Name = "4xtra-${var.environment}-private-${count.index}"
  }
}

# One shared route table for every private subnet: with no IGW and no NAT,
# there is no per-AZ routing difference to express. Gateway-endpoint routes
# are added to this table automatically by the aws_vpc_endpoint resources
# in endpoints.tf (route_table_ids).
resource "aws_route_table" "private" {
  vpc_id = aws_vpc.this.id

  tags = {
    Name = "4xtra-${var.environment}-private"
  }
}

resource "aws_route_table_association" "private" {
  count          = var.subnet_count
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private.id
}
