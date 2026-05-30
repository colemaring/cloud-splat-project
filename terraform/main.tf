provider "aws" {
  region = var.region
  default_tags {
    tags = var.tags
  }
}

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  # Bucket names must be globally unique; suffix with the account id.
  uploads_bucket = "${var.name_prefix}-uploads-${local.account_id}"
  outputs_bucket = "${var.name_prefix}-outputs-${local.account_id}"
  site_bucket    = "${var.name_prefix}-site-${local.account_id}"
}

module "storage" {
  source         = "./modules/storage"
  uploads_bucket = local.uploads_bucket
  outputs_bucket = local.outputs_bucket
  site_bucket    = local.site_bucket
}

module "data" {
  source      = "./modules/data"
  name_prefix = var.name_prefix
}

module "cdn" {
  source                  = "./modules/cdn"
  name_prefix             = var.name_prefix
  site_bucket_id          = module.storage.site_bucket_id
  site_bucket_arn         = module.storage.site_bucket_arn
  site_bucket_domain      = module.storage.site_bucket_regional_domain
  outputs_bucket_id       = module.storage.outputs_bucket_id
  outputs_bucket_arn      = module.storage.outputs_bucket_arn
  outputs_bucket_domain   = module.storage.outputs_bucket_regional_domain
}

module "compute" {
  source                = "./modules/compute"
  name_prefix           = var.name_prefix
  golden_ami_id         = var.golden_ami_id
  instance_type         = var.worker_instance_type
  root_volume_gb        = var.worker_root_volume_gb
  uploads_bucket_arn    = module.storage.uploads_bucket_arn
  outputs_bucket_arn    = module.storage.outputs_bucket_arn
  jobs_table_arn        = module.data.table_arn
}

module "lambda" {
  source                        = "./modules/lambda"
  name_prefix                   = var.name_prefix
  region                        = var.region
  jobs_table_name               = module.data.table_name
  jobs_table_arn                = module.data.table_arn
  jobs_table_gsi_arn            = module.data.table_gsi_arn
  uploads_bucket_name           = module.storage.uploads_bucket_id
  uploads_bucket_arn            = module.storage.uploads_bucket_arn
  outputs_bucket_name           = module.storage.outputs_bucket_id
  outputs_cdn_base              = "https://${module.cdn.distribution_domain}"
  launch_template_id            = module.compute.launch_template_id
  worker_role_arn               = module.compute.worker_role_arn
  max_runtime_secs              = var.max_runtime_secs
  worker_shutdown_guard_secs    = var.worker_shutdown_guard_secs
  reaper_max_instance_age_secs  = var.reaper_max_instance_age_secs
}

module "api" {
  source                  = "./modules/api"
  name_prefix             = var.name_prefix
  create_upload_url_arn   = module.lambda.create_upload_url_invoke_arn
  complete_upload_arn     = module.lambda.complete_upload_invoke_arn
  create_job_arn          = module.lambda.create_job_invoke_arn
  get_job_arn             = module.lambda.get_job_invoke_arn
  list_jobs_arn           = module.lambda.list_jobs_invoke_arn
  create_upload_url_name  = module.lambda.create_upload_url_name
  complete_upload_name    = module.lambda.complete_upload_name
  create_job_name         = module.lambda.create_job_name
  get_job_name            = module.lambda.get_job_name
  list_jobs_name          = module.lambda.list_jobs_name
}
