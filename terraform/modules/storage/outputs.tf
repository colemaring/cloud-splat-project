output "uploads_bucket_id" { value = aws_s3_bucket.uploads.id }
output "uploads_bucket_arn" { value = aws_s3_bucket.uploads.arn }

output "outputs_bucket_id" { value = aws_s3_bucket.outputs.id }
output "outputs_bucket_arn" { value = aws_s3_bucket.outputs.arn }
output "outputs_bucket_regional_domain" { value = aws_s3_bucket.outputs.bucket_regional_domain_name }

output "site_bucket_id" { value = aws_s3_bucket.site.id }
output "site_bucket_arn" { value = aws_s3_bucket.site.arn }
output "site_bucket_regional_domain" { value = aws_s3_bucket.site.bucket_regional_domain_name }
