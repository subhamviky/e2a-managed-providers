variable "tenant_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "image" {
  type        = string
  description = "OGX server image with distribution/config.yaml at /config (built and pushed to ECR by CI)."
}

variable "bedrock_model_ids" {
  type    = list(string)
  default = ["meta.llama4-maverick-17b-instruct-v1:0", "amazon.titan-embed-text-v2:0"]
}

variable "vpc_id" {
  type = string
}

variable "private_subnet_ids" {
  type = list(string)
}

variable "client_security_group_ids" {
  type = list(string)
}

variable "pgvector_host" {
  type = string
}

variable "pgvector_password_secret_arn" {
  type = string
}

variable "moderation_endpoint" {
  type        = string
  default     = ""
  description = "OpenAI-compatible moderation endpoint for OGX Responses guardrails; empty turns them off."
}

variable "desired_count" {
  type    = number
  default = 2
}
