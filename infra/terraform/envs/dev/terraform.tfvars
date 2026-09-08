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

# Phase 3b. control_plane_package_path has NO value here deliberately --
# see its description in variables.tf: it must be passed with -var at
# apply time, pointing at scripts/package_control_plane.py's actual output
# for that deployment (deploy-dev.yml, once extended, does this in CI).
alert_email = "todosgranja@gmail.com"
