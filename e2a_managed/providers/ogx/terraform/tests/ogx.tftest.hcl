mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  tenant_id                    = "acme"
  image                        = "123456789012.dkr.ecr.us-east-1.amazonaws.com/ogx:1"
  vpc_id                       = "vpc-1"
  private_subnet_ids           = ["subnet-1"]
  client_security_group_ids    = ["sg-1"]
  pgvector_host                = "pg.internal"
  pgvector_password_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:pg"
}

run "private_and_reachable_only_by_the_services" {
  command = plan
  assert {
    condition     = aws_ecs_service.this.network_configuration[0].assign_public_ip == false
    error_message = "the OGX service must be private"
  }
}

run "files_bucket_is_private_and_encrypted" {
  command = plan
  assert {
    condition     = aws_s3_bucket_public_access_block.files.block_public_acls && aws_s3_bucket_public_access_block.files.restrict_public_buckets
    error_message = "the OGX files bucket must block public access"
  }
}
