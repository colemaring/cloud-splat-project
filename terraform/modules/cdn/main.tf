locals {
  site_origin_id    = "site-s3"
  outputs_origin_id = "outputs-s3"
}

# One Origin Access Control shared by both S3 origins (SigV4).
resource "aws_cloudfront_origin_access_control" "s3" {
  name                              = "${var.name_prefix}-oac"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Rewrite pretty paths to the static .html files so /viewer/<id> and /gallery
# resolve. /outputs/* is a separate cache behavior and is NOT rewritten.
resource "aws_cloudfront_function" "rewrite" {
  name    = "${var.name_prefix}-rewrite"
  runtime = "cloudfront-js-2.0"
  comment = "Pretty-URL routing for the multi-page static app"
  publish = true
  code    = <<-EOT
    function handler(event) {
      var req = event.request;
      var uri = req.uri;
      if (uri === '/' || uri === '') { req.uri = '/index.html'; return req; }
      if (uri.startsWith('/viewer'))  { req.uri = '/viewer.html';  return req; }
      if (uri.startsWith('/status'))  { req.uri = '/status.html';  return req; }
      if (uri.startsWith('/gallery')) { req.uri = '/gallery.html'; return req; }
      return req;
    }
  EOT
}

# COOP/COEP on HTML so the page is cross-origin isolated (the Spark splat
# renderer relies on it, matching the local dev server's headers). The .ply is
# served same-origin under /outputs/*, so no CORP gymnastics are needed.
resource "aws_cloudfront_response_headers_policy" "site" {
  name = "${var.name_prefix}-site-headers"
  custom_headers_config {
    items {
      header   = "Cross-Origin-Opener-Policy"
      value    = "same-origin"
      override = true
    }
    items {
      header   = "Cross-Origin-Embedder-Policy"
      value    = "require-corp"
      override = true
    }
  }
}

# CORP on the .ply so a cross-origin-isolated page can load it under COEP.
resource "aws_cloudfront_response_headers_policy" "outputs" {
  name = "${var.name_prefix}-outputs-headers"
  custom_headers_config {
    items {
      header   = "Cross-Origin-Resource-Policy"
      value    = "same-origin"
      override = true
    }
  }
  cors_config {
    origin_override = true
    access_control_allow_credentials = false
    access_control_allow_headers { items = ["*"] }
    access_control_allow_methods { items = ["GET", "HEAD"] }
    access_control_allow_origins { items = ["*"] }
  }
}

resource "aws_cloudfront_distribution" "this" {
  enabled             = true
  default_root_object = "index.html"
  comment             = "${var.name_prefix} web app + splat outputs"
  price_class         = "PriceClass_100"

  origin {
    origin_id                = local.site_origin_id
    domain_name              = var.site_bucket_domain
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  origin {
    origin_id                = local.outputs_origin_id
    domain_name              = var.outputs_bucket_domain
    origin_access_control_id = aws_cloudfront_origin_access_control.s3.id
  }

  default_cache_behavior {
    target_origin_id       = local.site_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.site.id

    function_association {
      event_type   = "viewer-request"
      function_arn = aws_cloudfront_function.rewrite.arn
    }
  }

  ordered_cache_behavior {
    path_pattern           = "/outputs/*"
    target_origin_id       = local.outputs_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.outputs.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}

data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

# ── Bucket policies granting this distribution (only) read access ──────────────
data "aws_iam_policy_document" "site" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${var.site_bucket_arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = var.site_bucket_id
  policy = data.aws_iam_policy_document.site.json
}

data "aws_iam_policy_document" "outputs" {
  statement {
    actions   = ["s3:GetObject"]
    resources = ["${var.outputs_bucket_arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.this.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "outputs" {
  bucket = var.outputs_bucket_id
  policy = data.aws_iam_policy_document.outputs.json
}
