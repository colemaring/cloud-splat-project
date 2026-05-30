resource "aws_apigatewayv2_api" "http" {
  name          = "${var.name_prefix}-api"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "OPTIONS"]
    allow_headers = ["content-type"]
    max_age       = 3600
  }
}

resource "aws_apigatewayv2_stage" "default" {
  api_id      = aws_apigatewayv2_api.http.id
  name        = "$default"
  auto_deploy = true
}

locals {
  routes = {
    create_upload_url = { route_key = "POST /uploads",          arn = var.create_upload_url_arn, name = var.create_upload_url_name }
    complete_upload   = { route_key = "POST /uploads/complete", arn = var.complete_upload_arn,   name = var.complete_upload_name }
    create_job        = { route_key = "POST /jobs",             arn = var.create_job_arn,        name = var.create_job_name }
    get_job           = { route_key = "GET /jobs/{id}",         arn = var.get_job_arn,           name = var.get_job_name }
    list_jobs         = { route_key = "GET /jobs",              arn = var.list_jobs_arn,         name = var.list_jobs_name }
  }
}

resource "aws_apigatewayv2_integration" "fn" {
  for_each               = local.routes
  api_id                 = aws_apigatewayv2_api.http.id
  integration_type       = "AWS_PROXY"
  integration_uri        = each.value.arn
  integration_method     = "POST"
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "fn" {
  for_each  = local.routes
  api_id    = aws_apigatewayv2_api.http.id
  route_key = each.value.route_key
  target    = "integrations/${aws_apigatewayv2_integration.fn[each.key].id}"
}

resource "aws_lambda_permission" "apigw" {
  for_each      = local.routes
  statement_id  = "AllowApiGw-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = each.value.name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.http.execution_arn}/*/*"
}
