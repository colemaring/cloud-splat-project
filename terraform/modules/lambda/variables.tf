variable "name_prefix" { type = string }
variable "region" { type = string }

variable "jobs_table_name" { type = string }
variable "jobs_table_arn" { type = string }
variable "jobs_table_gsi_arn" { type = string }

variable "uploads_bucket_name" { type = string }
variable "uploads_bucket_arn" { type = string }
variable "outputs_bucket_name" { type = string }
variable "outputs_cdn_base" { type = string }

variable "launch_template_id" { type = string }
variable "worker_role_arn" { type = string }

variable "max_runtime_secs" { type = number }
variable "worker_shutdown_guard_secs" { type = number }
variable "reaper_max_instance_age_secs" { type = number }
