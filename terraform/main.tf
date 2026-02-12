provider "aws" {
  region = var.aws_region
}

# --------------------------------------------------------------------------------
# S3 Bucket for Search Results
# --------------------------------------------------------------------------------
resource "aws_s3_bucket" "source_bucket" {
  bucket = "${var.project_name}-source-${var.environment}"
}

resource "aws_s3_bucket_notification" "bucket_notification" {
  bucket      = aws_s3_bucket.source_bucket.id
  eventbridge = true
}

# --------------------------------------------------------------------------------
# IAM Role for Lambdas
# --------------------------------------------------------------------------------
resource "aws_iam_role" "lambda_role" {
  name = "${var.project_name}-lambda-role-${var.environment}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = "sts:AssumeRole"
        Effect = "Allow"
        Principal = {
          Service = "lambda.amazonaws.com"
        }
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_iam_role_policy_attachment" "lambda_vpc" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaVPCAccessExecutionRole"
}

# Policy for S3 Access
resource "aws_iam_policy" "s3_access" {
  name        = "${var.project_name}-s3-access-${var.environment}"
  description = "Allow access to source bucket"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:ListBucket"
        ]
        Effect   = "Allow"
        Resource = [
          aws_s3_bucket.source_bucket.arn,
          "${aws_s3_bucket.source_bucket.arn}/*"
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_s3" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.s3_access.arn
}

# Policy for RDS Data Access (if using Data API) or generic RDS connect
resource "aws_iam_policy" "rds_access" {
  name        = "${var.project_name}-rds-access-${var.environment}"
  description = "Allow access to Aurora RDS"

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Action = [
          "rds-db:connect"
        ]
        Effect   = "Allow"
        Resource = "*" # Restrict to specific DB ARN in production
      }
    ]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_rds" {
  role       = aws_iam_role.lambda_role.name
  policy_arn = aws_iam_policy.rds_access.arn
}

# --------------------------------------------------------------------------------
# Lambda Functions
# --------------------------------------------------------------------------------

# Archive files (In real pipeline, these would be built artifacts)
data "archive_file" "ingestion_zip" {
  type        = "zip"
  source_file = "${path.module}/../tavily_ingestion_lambda.py"
  output_path = "${path.module}/ingestion.zip"
}

data "archive_file" "loader_zip" {
  type        = "zip"
  source_file = "${path.module}/../tavily_loader_lambda.py"
  output_path = "${path.module}/loader.zip"
}

# Ingestion Lambda
resource "aws_lambda_function" "ingestion_lambda" {
  filename         = data.archive_file.ingestion_zip.output_path
  function_name    = "${var.project_name}-ingestion-${var.environment}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "tavily_ingestion_lambda.lambda_handler"
  runtime          = "python3.9"
  timeout          = 300
  source_code_hash = data.archive_file.ingestion_zip.output_base64sha256

  environment {
    variables = {
      S3_BUCKET      = aws_s3_bucket.source_bucket.id
      TAVILY_API_KEY = var.tavily_api_key
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [] # Add SG if needed
  }
}

# Loader Lambda
resource "aws_lambda_function" "loader_lambda" {
  filename         = data.archive_file.loader_zip.output_path
  function_name    = "${var.project_name}-loader-${var.environment}"
  role             = aws_iam_role.lambda_role.arn
  handler          = "tavily_loader_lambda.lambda_handler"
  runtime          = "python3.9"
  timeout          = 300
  source_code_hash = data.archive_file.loader_zip.output_base64sha256

  environment {
    variables = {
      S3_BUCKET = aws_s3_bucket.source_bucket.id
      DB_HOST   = "agsa-dev-db-cluster.cluster-cxaiaiqu27x7.af-south-1.rds.amazonaws.com" # Should be var
      DB_NAME   = "agsa-content"
      DB_USER   = "agsaadmin"
    }
  }

  vpc_config {
    subnet_ids         = var.subnet_ids
    security_group_ids = [] # Add SG if needed
  }
}

# --------------------------------------------------------------------------------
# EventBridge Rule (S3 Trigger)
# --------------------------------------------------------------------------------
resource "aws_cloudwatch_event_rule" "s3_upload_rule" {
  name        = "${var.project_name}-s3-upload-rule-${var.environment}"
  description = "Trigger Loader Lambda when object created in S3"

  event_pattern = jsonencode({
    source      = ["aws.s3"]
    detail-type = ["Object Created"]
    detail = {
      bucket = {
        name = [aws_s3_bucket.source_bucket.id]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "s3_upload_target" {
  rule      = aws_cloudwatch_event_rule.s3_upload_rule.name
  target_id = "TriggerLoaderLambda"
  arn       = aws_lambda_function.loader_lambda.arn
}

resource "aws_lambda_permission" "allow_eventbridge" {
  statement_id  = "AllowExecutionFromEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.loader_lambda.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.s3_upload_rule.arn
}
