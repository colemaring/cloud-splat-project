output "create_upload_url_invoke_arn" { value = aws_lambda_function.fn["create_upload_url"].invoke_arn }
output "complete_upload_invoke_arn" { value = aws_lambda_function.fn["complete_upload"].invoke_arn }
output "create_job_invoke_arn" { value = aws_lambda_function.fn["create_job"].invoke_arn }
output "get_job_invoke_arn" { value = aws_lambda_function.fn["get_job"].invoke_arn }
output "list_jobs_invoke_arn" { value = aws_lambda_function.fn["list_jobs"].invoke_arn }

output "create_upload_url_name" { value = aws_lambda_function.fn["create_upload_url"].function_name }
output "complete_upload_name" { value = aws_lambda_function.fn["complete_upload"].function_name }
output "create_job_name" { value = aws_lambda_function.fn["create_job"].function_name }
output "get_job_name" { value = aws_lambda_function.fn["get_job"].function_name }
output "list_jobs_name" { value = aws_lambda_function.fn["list_jobs"].function_name }
