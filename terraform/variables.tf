variable "aws_region" {
  description = "AWS region to deploy resources"
  default     = "af-south-1"
}

variable "project_name" {
  description = "Project name for tagging and naming"
  default     = "agsa-corruption-search"
}

variable "environment" {
  description = "Environment (dev, prod)"
  default     = "dev"
}

variable "vpc_id" {
  description = "VPC ID where Aurora and Lambdas reside"
  type        = string
  # Provide default or leave empty to force input
  default     = "" 
}

variable "subnet_ids" {
  description = "Subnet IDs for Lambda functions"
  type        = list(string)
  default     = []
}

variable "aurora_cluster_arn" {
  description = "ARN of the existing Aurora Cluster"
  type        = string
  default     = ""
}

variable "db_secret_arn" {
  description = "ARN of the Secrets Manager secret for DB credentials"
  type        = string
  default     = ""
}

variable "tavily_api_key" {
  description = "API Key for Tavily"
  type        = string
  sensitive   = true
}
