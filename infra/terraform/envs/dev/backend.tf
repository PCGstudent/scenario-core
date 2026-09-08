# Remote state in the bucket infra/terraform/bootstrap created, in this
# same DEV account, versioned and KMS-encrypted (bootstrap output), with S3
# native locking (`use_lockfile = true`, Terraform 1.10+) -- no DynamoDB
# lock table (Section 17: "one fewer resource, and the DynamoDB locking
# mechanism is deprecated").
#
# This bucket name is deterministic (bootstrap/main.tf:
# "4xtra-{environment}-tfstate-{account_id}") and is duplicated here
# literally rather than looked up, because a backend configuration block
# cannot reference a variable, a data source, or another module's output --
# backend initialization happens before any provider or module is
# evaluated. If bootstrap ever produces a different bucket (a different
# account), update this literal to match (see bootstrap/README.md step 2).
#
# `kms_key_id` is required, not optional decoration: the bucket policy
# (infra/terraform/bootstrap) denies any PutObject whose
# `s3:x-amz-server-side-encryption` header is not literally `aws:kms` --
# `encrypt = true` alone does not guarantee that. Without `kms_key_id`,
# the S3 backend's default SSE algorithm is `AES256` (SSE-S3), which the
# bucket policy's own `DenyUnencryptedObjectUploads` statement rejects,
# for both the state object and the S3-native lockfile
# (`terraform.tfstate.tflock`) the same `use_lockfile = true` mechanism
# above writes -- confirmed directly: `terraform plan` failed with
# `AccessDenied ... with an explicit deny in a resource-based policy`
# on the lockfile PutObject before this key was added.
#
# The alias below is the BOOTSTRAP state-bucket CMK
# (`alias/4xtra-dev-tfstate`, infra/terraform/bootstrap's own key), not
# the environment CMK `module.kms` creates -- deliberately: the backend
# must be usable before `module.kms` has ever been applied (this root's
# own remote state has to exist for `module.kms` to be planned into it in
# the first place), so it can only ever depend on something bootstrap
# already created, never on anything this root configuration itself
# manages.
terraform {
  backend "s3" {
    bucket       = "4xtra-dev-tfstate-758895552145"
    key          = "envs/dev/terraform.tfstate"
    region       = "eu-west-1"
    use_lockfile = true
    encrypt      = true
    kms_key_id   = "alias/4xtra-dev-tfstate"
  }
}
