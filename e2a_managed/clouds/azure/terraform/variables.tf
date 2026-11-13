variable "tenant_id" {
  type = string
}

variable "tenancy_model" {
  type    = string
  default = "POOL"
  validation {
    condition     = contains(["POOL", "SILO"], var.tenancy_model)
    error_message = "tenancy_model must be POOL or SILO."
  }
}

variable "availability_tier" {
  type    = string
  default = "STANDARD"
}

variable "location" {
  type    = string
  default = "eastus2"
}

variable "zone_redundant" {
  type    = bool
  default = false
}

variable "chat_deployment" {
  type        = string
  description = "Deployment name = the profile's model_id."
  default     = "gpt-5"
}

variable "chat_model" {
  type    = string
  default = "gpt-5"
}

variable "chat_model_version" {
  type    = string
  default = "2025-08-07"
}

variable "chat_capacity_k_tpm" {
  type    = number
  default = 100
}

variable "embedding_deployment" {
  type    = string
  default = "text-embedding-3-large"
}

variable "search_index" {
  type    = string
  default = "e2a-knowledge"
}
