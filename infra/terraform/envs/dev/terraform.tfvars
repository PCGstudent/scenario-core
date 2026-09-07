environment = "dev"
region      = "eu-west-1"
project     = "4xtra"
owner       = "4xtra-platform-team"
cost_center = "4xtra-platform"

subnet_count          = 2
flow_log_traffic_type = "REJECT"

enable_object_lock = false
runs_expire_days   = 90

image_count_to_retain = 5

github_repository = "PCGstudent/scenario-core"
github_ref        = "ref:refs/heads/main"
