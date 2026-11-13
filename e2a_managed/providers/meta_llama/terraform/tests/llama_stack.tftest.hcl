mock_provider "aws" {
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  tenant_id                    = "acme"
  image                        = "123456789012.dkr.ecr.us-east-1.amazonaws.com/llama-stack:1"
  vpc_id                       = "vpc-1"
  private_subnet_ids           = ["subnet-1"]
  client_security_group_ids    = ["sg-1"]
  pgvector_host                = "pg.internal"
  pgvector_password_secret_arn = "arn:aws:secretsmanager:us-east-1:123456789012:secret:pg"
  llama_guard_url              = "http://guard.internal:8000/v1"
}

run "private_and_reachable_only_by_the_services" {
  command = plan
  assert {
    condition     = aws_ecs_service.this.network_configuration[0].assign_public_ip == false
    error_message = "the Llama Stack service must be private"
  }
}
