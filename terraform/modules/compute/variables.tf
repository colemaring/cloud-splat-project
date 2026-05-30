variable "name_prefix" { type = string }
variable "golden_ami_id" { type = string }
variable "instance_type" { type = string }
variable "root_volume_gb" { type = number }
variable "uploads_bucket_arn" { type = string }
variable "outputs_bucket_arn" { type = string }
variable "jobs_table_arn" { type = string }
