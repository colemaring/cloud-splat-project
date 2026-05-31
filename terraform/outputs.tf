output "site_url" {
  description = "Public URL of the web app (CloudFront)."
  value       = "https://${module.cdn.distribution_domain}"
}

output "api_endpoint" {
  description = "Base URL of the HTTP API. Inject into the frontend build as VITE_API_BASE."
  value       = module.api.api_endpoint
}

output "cdn_domain" {
  description = "CloudFront domain serving both the site and /outputs/*.ply."
  value       = module.cdn.distribution_domain
}

output "cloudfront_distribution_id" {
  description = "CloudFront distribution id (for cache invalidation on redeploy)."
  value       = module.cdn.distribution_id
}

output "site_bucket" {
  description = "S3 bucket for the built frontend (aws s3 sync your dist/ here)."
  value       = module.storage.site_bucket_id
}

output "uploads_bucket" {
  value = module.storage.uploads_bucket_id
}

output "outputs_bucket" {
  value = module.storage.outputs_bucket_id
}

output "jobs_table" {
  value = module.data.table_name
}

output "launch_template_id" {
  value = module.compute.launch_template_id
}
