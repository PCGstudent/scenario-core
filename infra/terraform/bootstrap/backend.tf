# Deliberately no `backend` block: this configuration's own state stays
# LOCAL, permanently, on purpose (IMPLEMENTATION_PLAN.md Section 17:
# "applied once with local state, then migrated" -- the thing that gets
# migrated to the bucket this config creates is envs/dev's (and later
# envs/prod's) backend, not bootstrap's own).
#
# Why bootstrap does not self-host its own state in the bucket it creates:
# the state bucket does not exist on `terraform init` for a fresh account,
# so bootstrap cannot start from a remote backend that presupposes its own
# output. It could, in principle, migrate itself to the bucket in a SECOND
# `terraform init -migrate-state` once the bucket exists -- but bootstrap's
# resource count is small and nearly static (one bucket, one CMK), the
# operational risk of a chicken-and-egg backend pointing at a bucket that a
# future `terraform destroy` of bootstrap itself could remove is worse than
# the (small, reviewable) risk of local state for this one directory, and a
# local bootstrap state file is easy to keep safe: run bootstrap from a
# single trusted machine, back the state file up out of band (it contains
# no secrets -- only resource IDs and ARNs), and never run `terraform apply`
# here except as part of the documented, deliberate first-time-per-account
# sequence in this directory's README.
#
# Every environment THIS bootstrap serves (envs/dev, envs/prod) uses the
# real S3 + native-locking remote backend, pointed at the bucket created
# here. See envs/dev/backend.tf.
