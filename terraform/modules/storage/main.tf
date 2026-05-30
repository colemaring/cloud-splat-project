# ── Uploads bucket: browser PUTs .insv parts here via presigned multipart ──────
resource "aws_s3_bucket" "uploads" {
  bucket = var.uploads_bucket
}

resource "aws_s3_bucket_public_access_block" "uploads" {
  bucket                  = aws_s3_bucket.uploads.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_cors_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id
  cors_rule {
    allowed_methods = ["PUT", "POST", "GET", "HEAD"]
    allowed_origins = ["*"]
    allowed_headers = ["*"]
    # ETag MUST be exposed or the browser can't read it to complete multipart.
    expose_headers  = ["ETag"]
    max_age_seconds = 3600
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "uploads" {
  bucket = aws_s3_bucket.uploads.id

  rule {
    id     = "abort-incomplete-multipart"
    status = "Enabled"
    filter {}
    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }

  rule {
    id     = "expire-inputs"
    status = "Enabled"
    filter {}
    # Backstop: the worker deletes its own input when done. This catches
    # inputs from jobs that never ran.
    expiration {
      days = 2
    }
  }
}

# ── Outputs bucket: trained scene.ply, served via CloudFront only ──────────────
resource "aws_s3_bucket" "outputs" {
  bucket = var.outputs_bucket
}

resource "aws_s3_bucket_public_access_block" "outputs" {
  bucket                  = aws_s3_bucket.outputs.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ── Site bucket: built static frontend, served via CloudFront only ─────────────
resource "aws_s3_bucket" "site" {
  bucket = var.site_bucket
}

resource "aws_s3_bucket_public_access_block" "site" {
  bucket                  = aws_s3_bucket.site.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}
