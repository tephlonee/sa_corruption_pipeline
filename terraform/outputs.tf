output "s3_bucket_name" {
  value = aws_s3_bucket.source_bucket.id
}

output "ingestion_lambda_arn" {
  value = aws_lambda_function.ingestion_lambda.arn
}

output "loader_lambda_arn" {
  value = aws_lambda_function.loader_lambda.arn
}
