output "launch_template_id" { value = aws_launch_template.worker.id }
output "worker_role_arn" { value = aws_iam_role.worker.arn }
