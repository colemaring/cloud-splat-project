terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.40"
    }
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }
  }

  # Configure a remote backend for real use (recommended):
  #   backend "s3" {
  #     bucket         = "your-tf-state-bucket"
  #     key            = "cloud-splat/terraform.tfstate"
  #     region         = "us-east-1"
  #     dynamodb_table = "your-tf-lock-table"
  #   }
}
