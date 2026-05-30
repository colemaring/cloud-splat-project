locals {
  runtime     = "python3.12"
  source_dir  = "${path.module}/../../../lambda"
  log_actions = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
}

# Single zip of the whole lambda/ tree; every function shares it and selects its
# own entrypoint via `handler` (e.g. create_job.handler.handler). boto3 is in
# the Lambda runtime so there are no third-party deps to bundle.
data "archive_file" "pkg" {
  type        = "zip"
  source_dir  = local.source_dir
  output_path = "${path.module}/build/lambda.zip"
}

data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

# ── role factory via a small module-local pattern ──────────────────────────────
# Each function gets its own role + an inline policy passed as JSON.
resource "aws_iam_role" "fn" {
  for_each           = local.role_policies
  name               = "${var.name_prefix}-${each.key}"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

resource "aws_iam_role_policy" "fn" {
  for_each = local.role_policies
  name     = "${var.name_prefix}-${each.key}"
  role     = aws_iam_role.fn[each.key].id
  policy   = each.value
}

locals {
  role_policies = {
    create_upload_url = jsonencode({
      Version = "2012-10-17"
      Statement = [
        { Effect = "Allow", Action = local.log_actions, Resource = "*" },
        { Effect = "Allow",
          Action = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
          Resource = "${var.uploads_bucket_arn}/*" },
      ]
    })
    complete_upload = jsonencode({
      Version = "2012-10-17"
      Statement = [
        { Effect = "Allow", Action = local.log_actions, Resource = "*" },
        { Effect = "Allow",
          Action = ["s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"],
          Resource = "${var.uploads_bucket_arn}/*" },
      ]
    })
    create_job = jsonencode({
      Version = "2012-10-17"
      Statement = [
        { Effect = "Allow", Action = local.log_actions, Resource = "*" },
        { Effect = "Allow", Action = ["dynamodb:PutItem", "dynamodb:UpdateItem"], Resource = var.jobs_table_arn },
        # RunInstances touches many resource types (template, AMI, subnet, SG,
        # instance, ENI, volume); scoping to all of them is impractical, so we
        # allow the action broadly. The sensitive grant — PassRole — is scoped
        # to the worker role only.
        { Effect = "Allow", Action = ["ec2:RunInstances", "ec2:CreateTags"], Resource = "*" },
        { Effect = "Allow", Action = ["iam:PassRole"], Resource = var.worker_role_arn },
      ]
    })
    get_job = jsonencode({
      Version = "2012-10-17"
      Statement = [
        { Effect = "Allow", Action = local.log_actions, Resource = "*" },
        { Effect = "Allow", Action = ["dynamodb:GetItem"], Resource = var.jobs_table_arn },
      ]
    })
    list_jobs = jsonencode({
      Version = "2012-10-17"
      Statement = [
        { Effect = "Allow", Action = local.log_actions, Resource = "*" },
        { Effect = "Allow", Action = ["dynamodb:Query"], Resource = var.jobs_table_gsi_arn },
      ]
    })
    reaper = jsonencode({
      Version = "2012-10-17"
      Statement = [
        { Effect = "Allow", Action = local.log_actions, Resource = "*" },
        { Effect = "Allow", Action = ["ec2:DescribeInstances"], Resource = "*" },
        { Effect = "Allow", Action = ["ec2:TerminateInstances"], Resource = "*",
          Condition = { StringEquals = { "ec2:ResourceTag/Project" = "cloud-splat" } } },
        { Effect = "Allow", Action = ["dynamodb:GetItem", "dynamodb:UpdateItem"], Resource = var.jobs_table_arn },
      ]
    })
  }

  fn_env = {
    create_upload_url = { UPLOADS_BUCKET = var.uploads_bucket_name }
    complete_upload   = { UPLOADS_BUCKET = var.uploads_bucket_name }
    create_job = {
      JOBS_TABLE                 = var.jobs_table_name
      UPLOADS_BUCKET             = var.uploads_bucket_name
      OUTPUTS_BUCKET             = var.outputs_bucket_name
      LAUNCH_TEMPLATE_ID         = var.launch_template_id
      MAX_RUNTIME_SECS           = tostring(var.max_runtime_secs)
      WORKER_SHUTDOWN_GUARD_SECS = tostring(var.worker_shutdown_guard_secs)
    }
    get_job   = { JOBS_TABLE = var.jobs_table_name, OUTPUTS_CDN_BASE = var.outputs_cdn_base }
    list_jobs = { JOBS_TABLE = var.jobs_table_name, OUTPUTS_CDN_BASE = var.outputs_cdn_base }
    reaper    = { JOBS_TABLE = var.jobs_table_name, MAX_INSTANCE_AGE_SECS = tostring(var.reaper_max_instance_age_secs) }
  }

  fn_timeout = {
    create_upload_url = 30
    complete_upload   = 30
    create_job        = 30
    get_job           = 10
    list_jobs         = 10
    reaper            = 60
  }
}

resource "aws_lambda_function" "fn" {
  for_each         = local.role_policies
  function_name    = "${var.name_prefix}-${each.key}"
  role             = aws_iam_role.fn[each.key].arn
  runtime          = local.runtime
  handler          = "${each.key}.handler.handler"
  filename         = data.archive_file.pkg.output_path
  source_code_hash = data.archive_file.pkg.output_base64sha256
  timeout          = local.fn_timeout[each.key]
  memory_size      = 256

  environment {
    variables = local.fn_env[each.key]
  }
}

# ── Reaper schedule (cost-safety backstop) ─────────────────────────────────────
resource "aws_cloudwatch_event_rule" "reaper" {
  name                = "${var.name_prefix}-reaper"
  schedule_expression = "rate(30 minutes)"
}

resource "aws_cloudwatch_event_target" "reaper" {
  rule = aws_cloudwatch_event_rule.reaper.name
  arn  = aws_lambda_function.fn["reaper"].arn
}

resource "aws_lambda_permission" "reaper_events" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["reaper"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.reaper.arn
}
