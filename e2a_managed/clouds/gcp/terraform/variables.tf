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

variable "project_id" {
  type = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "vector_index_name" {
  type    = string
  default = "e2a-knowledge"
}

variable "embedding_dimensions" {
  type    = number
  default = 768
}
