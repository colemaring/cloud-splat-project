resource "aws_dynamodb_table" "jobs" {
  name         = "${var.name_prefix}-jobs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "jobId"

  attribute {
    name = "jobId"
    type = "S"
  }

  # GSI partition key — constant "JOB" so the gallery can Query (not Scan) all
  # jobs ordered by createdAt.
  attribute {
    name = "gsiPk"
    type = "S"
  }

  attribute {
    name = "createdAt"
    type = "N"
  }

  global_secondary_index {
    name            = "byCreated"
    hash_key        = "gsiPk"
    range_key       = "createdAt"
    projection_type = "ALL"
  }
}
