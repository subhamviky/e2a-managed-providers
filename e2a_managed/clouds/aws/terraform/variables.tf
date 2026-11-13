variable "tenant_id" {
  type        = string
  description = "Tenant the landing zone serves."
}

variable "tenancy_model" {
  type        = string
  description = "POOL (shared tables, namespace filters) or SILO."
  default     = "POOL"
  validation {
    condition     = contains(["POOL", "SILO"], var.tenancy_model)
    error_message = "tenancy_model must be POOL or SILO."
  }
}

variable "availability_tier" {
  type    = string
  default = "STANDARD"
}

variable "region" {
  type    = string
  default = "us-east-1"
}

variable "az_count" {
  type    = number
  default = 1
}

variable "model_ids" {
  type        = list(string)
  description = "Bedrock model / inference-profile ids the services may invoke (profile model_id, fallback)."
  default     = ["anthropic.claude-opus-5-5"]
}

variable "embedding_model_id" {
  type    = string
  default = "amazon.titan-embed-text-v2:0"
}

variable "vector_index_name" {
  type    = string
  default = "e2a-knowledge"
}

variable "idempotency_table" {
  type    = string
  default = "e2a-idempotency"
}

variable "checkpoint_table" {
  type    = string
  default = "e2a-checkpoints"
}
