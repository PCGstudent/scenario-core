variable "environment" {
  type        = string
  description = "Environment name, used in resource naming."
}

variable "region" {
  type        = string
  description = "AWS region."
}

variable "vpc_cidr" {
  type        = string
  description = "VPC CIDR block."
  default     = "10.20.0.0/16"
}

variable "subnet_count" {
  type        = number
  description = "Number of private subnets / AZs. Section 14.1 point 6: the ONLY topology difference between DEV and PROD -- DEV=2, PROD=3. Everything else (route tables, security groups, endpoint policies) is identical."
  nullable    = false

  validation {
    condition     = var.subnet_count >= 2 && var.subnet_count <= 6
    error_message = "subnet_count must be between 2 and 6 (AWS regions used here have at most 6 AZs)."
  }
}

variable "kms_key_arn" {
  type        = string
  description = "Per-environment CMK ARN, used to encrypt the flow-logs bucket."
}

variable "s3_bucket_arns" {
  type        = list(string)
  description = "Artifacts + runs bucket ARNs (modules/artifact_store outputs), for the S3 gateway endpoint policy (Section 14.1: restricted to the two project buckets)."
}

variable "dynamodb_table_arns" {
  type        = list(string)
  description = "Jobs + registry table ARNs (modules/job_store outputs), for the DynamoDB gateway endpoint policy."
}

variable "ecr_repository_arn" {
  type        = string
  description = "Worker ECR repository ARN (modules/worker_image output), for the ecr.api/ecr.dkr endpoint policies (Section 14.1: restricted to the one repository)."
}

variable "flow_log_traffic_type" {
  type        = string
  description = "VPC flow log traffic type. Section 13.4: REJECT only in DEV, ALL in PROD."
  default     = "REJECT"

  validation {
    condition     = contains(["ACCEPT", "REJECT", "ALL"], var.flow_log_traffic_type)
    error_message = "flow_log_traffic_type must be ACCEPT, REJECT or ALL."
  }
}

variable "flow_log_expire_days" {
  type        = number
  description = "Days before a flow-log object expires. Mirrors the log-retention convention (Section 15.1: 30 d DEV / 400 d PROD)."
  default     = 30
}
