variable "region" {
  description = "AWS region for all resources (CloudFront is global)."
  type        = string
  default     = "us-east-1"
}

variable "name_prefix" {
  description = "Prefix for resource names. Bucket names append the account id for global uniqueness."
  type        = string
  default     = "cloud-splat"
}

variable "golden_ami_id" {
  description = "AMI id of the pre-baked GPU worker image (see scripts/build_ami.sh). Required to launch jobs."
  type        = string
}

variable "worker_instance_type" {
  description = "EC2 instance type for the per-job GPU worker. Bump to g5/g6 if the T4 (g4dn) OOMs on High preset."
  type        = string
  default     = "g4dn.xlarge"
}

variable "worker_root_volume_gb" {
  description = "Root gp3 volume size for the worker (frames + cubemaps + COLMAP + training scratch)."
  type        = number
  default     = 200
}

variable "max_runtime_secs" {
  description = "In-worker hard wall-clock cap before it self-terminates."
  type        = number
  default     = 21600 # 6h
}

variable "worker_shutdown_guard_secs" {
  description = "user-data sleep guard that shuts the box down even if the worker process never starts. Keep > max_runtime_secs."
  type        = number
  default     = 25200 # 7h
}

variable "reaper_max_instance_age_secs" {
  description = "The scheduled reaper terminates any worker older than this. Keep > worker_shutdown_guard_secs."
  type        = number
  default     = 28800 # 8h
}

variable "tags" {
  description = "Tags applied to all resources."
  type        = map(string)
  default = {
    Project = "cloud-splat"
  }
}
