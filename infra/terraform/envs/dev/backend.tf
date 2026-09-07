# Remote state in the bucket infra/terraform/bootstrap created, in this
# same DEV account, versioned and KMS-encrypted (bootstrap output), with S3
# native locking (`use_lockfile = true`, Terraform 1.10+) -- no DynamoDB
# lock table (Section 17: "one fewer resource, and the DynamoDB locking
# mechanism is deprecated").
#
# This bucket name is deterministic (bootstrap/main.tf:
# "4xtra-{environment}-tfstate-{account_id}") and is duplicated here
# literally rather than looked up, because a backend configuration block
# cannot reference a variable or another module's output -- backend
# initialization happens before any provider or module is evaluated. If
# bootstrap ever produces a different bucket (a different account),
# update this literal to match (see bootstrap/README.md step 2).
terraform {
  backend "s3" {
    bucket       = "4xtra-dev-tfstate-758895552145"
    key          = "envs/dev/terraform.tfstate"
    region       = "eu-west-1"
    use_lockfile = true
    encrypt      = true
  }
}
