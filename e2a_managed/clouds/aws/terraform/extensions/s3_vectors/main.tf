# S3 Vectors bucket + index for S3VectorsIndex. Kept apart from the core module because the
# resource types are recent: pin a hashicorp/aws release that includes them and check the
# attribute names against its documentation before the first apply. CI validates this module
# in a non-blocking job.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.10"
    }
  }
}

variable "tenant_id" { type = string }
variable "vector_index_name" {
  type    = string
  default = "e2a-knowledge"
}
variable "embedding_dimensions" {
  type    = number
  default = 1024
}

resource "aws_s3vectors_vector_bucket" "this" {
  vector_bucket_name = "e2a-${var.tenant_id}-vectors"
}

resource "aws_s3vectors_index" "this" {
  vector_bucket_name = aws_s3vectors_vector_bucket.this.vector_bucket_name
  index_name         = var.vector_index_name
  data_type          = "float32"
  dimension          = var.embedding_dimensions
  distance_metric    = "cosine"
  metadata_configuration {
    non_filterable_metadata_keys = ["text"] # passage text is returned, never filtered on
  }
}

output "S3_VECTOR_BUCKET" {
  value = aws_s3vectors_vector_bucket.this.vector_bucket_name
}
