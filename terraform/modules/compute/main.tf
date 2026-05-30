# Use the account's default VPC + a public subnet so the worker gets a public
# IP and can reach S3/DynamoDB/EC2 APIs without extra networking.
data "aws_vpc" "default" {
  default = true
}

data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
}

resource "aws_security_group" "worker" {
  name        = "${var.name_prefix}-worker"
  description = "Egress-only SG for ephemeral GPU workers"
  vpc_id      = data.aws_vpc.default.id

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }
}

# ── Worker instance role ───────────────────────────────────────────────────────
data "aws_iam_policy_document" "assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "worker" {
  name               = "${var.name_prefix}-worker"
  assume_role_policy = data.aws_iam_policy_document.assume.json
}

data "aws_iam_policy_document" "worker" {
  statement {
    sid       = "ReadDeleteInputs"
    actions   = ["s3:GetObject", "s3:DeleteObject"]
    resources = ["${var.uploads_bucket_arn}/*"]
  }
  statement {
    sid       = "WriteOutputs"
    actions   = ["s3:PutObject"]
    resources = ["${var.outputs_bucket_arn}/*"]
  }
  statement {
    sid       = "UpdateJobStatus"
    actions   = ["dynamodb:GetItem", "dynamodb:UpdateItem"]
    resources = [var.jobs_table_arn]
  }
}

resource "aws_iam_role_policy" "worker" {
  name   = "${var.name_prefix}-worker"
  role   = aws_iam_role.worker.id
  policy = data.aws_iam_policy_document.worker.json
}

resource "aws_iam_instance_profile" "worker" {
  name = "${var.name_prefix}-worker"
  role = aws_iam_role.worker.name
}

# ── Launch template ────────────────────────────────────────────────────────────
# The per-job user-data is supplied by the create_job Lambda at RunInstances
# time. instance_initiated_shutdown_behavior=terminate lets the worker
# self-terminate with `shutdown -h now` (no ec2:TerminateInstances grant needed).
resource "aws_launch_template" "worker" {
  name                   = "${var.name_prefix}-worker"
  image_id               = var.golden_ami_id
  instance_type          = var.instance_type
  update_default_version = true

  instance_initiated_shutdown_behavior = "terminate"

  iam_instance_profile {
    arn = aws_iam_instance_profile.worker.arn
  }

  network_interfaces {
    associate_public_ip_address = true
    security_groups             = [aws_security_group.worker.id]
    delete_on_termination       = true
  }

  block_device_mappings {
    device_name = "/dev/sda1"
    ebs {
      volume_size           = var.root_volume_gb
      volume_type           = "gp3"
      delete_on_termination = true
    }
  }

  tag_specifications {
    resource_type = "instance"
    tags = {
      Project = "cloud-splat"
    }
  }
}
