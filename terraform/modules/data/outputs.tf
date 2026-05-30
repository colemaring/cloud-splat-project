output "table_name" { value = aws_dynamodb_table.jobs.name }
output "table_arn" { value = aws_dynamodb_table.jobs.arn }
output "table_gsi_arn" { value = "${aws_dynamodb_table.jobs.arn}/index/byCreated" }
