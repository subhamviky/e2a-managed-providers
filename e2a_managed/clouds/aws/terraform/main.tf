# Managed-AI substrate for E2A services on AWS, shared by the anthropic_* and meta_llama_aws
# profiles. Values come from AWSInfraProvisioner (<tenant>.auto.tfvars.json). Applied by CI
# with GitHub OIDC; tested offline with `terraform test` against a mock provider.
terraform {
  required_version = ">= 1.7"
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = var.region
}

locals {
  name = "e2a-${var.tenant_id}"
  tags = { "e2a:tenant" = var.tenant_id, "e2a:tenancy" = var.tenancy_model, "e2a:tier" = var.availability_tier }
  # Bedrock model ARNs the Tier-2 role may invoke: foundation models and cross-region profiles.
  model_arns = flatten([for m in var.model_ids : [
    "arn:aws:bedrock:*::foundation-model/${trimprefix(m, "us.")}",
    "arn:aws:bedrock:${var.region}:${data.aws_caller_identity.this.account_id}:inference-profile/${m}",
  ]])
}

data "aws_caller_identity" "this" {}

# ---- E2A state (e2a-adapters' DynamoDB stores, bound by its "aws" profile): idempotency claims,
# approval checkpoints, and versioned rows and leases; one item per key under pk/sk, every change
# one conditional write
resource "aws_dynamodb_table" "state" {
  for_each     = { idempotency = var.idempotency_table, checkpoints = var.checkpoint_table, state = var.state_table }
  name         = each.value
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  ttl {
    attribute_name = "ttl" # garbage collection only; correctness uses expires_at in the record
    enabled        = true
  }

  point_in_time_recovery {
    enabled = true
  }

  server_side_encryption {
    enabled = true
  }

  tags = local.tags
}

# ---- Bedrock Guardrails: prompt attacks and harmful content blocked, PII masked or blocked
resource "aws_bedrock_guardrail" "input" {
  name                      = "${local.name}-input"
  blocked_input_messaging   = "This request was blocked by the platform's safety policy."
  blocked_outputs_messaging = "The response was blocked by the platform's safety policy."
  description               = "E2A ingress guardrail (BedrockGuardrailStage)"

  content_policy_config {
    dynamic "filters_config" {
      for_each = ["HATE", "INSULTS", "SEXUAL", "VIOLENCE", "MISCONDUCT"]
      content {
        type            = filters_config.value
        input_strength  = "HIGH"
        output_strength = "HIGH"
      }
    }
    filters_config {
      type            = "PROMPT_ATTACK"
      input_strength  = "HIGH"
      output_strength = "NONE"
    }
  }

  sensitive_information_policy_config {
    pii_entities_config {
      type   = "EMAIL"
      action = "ANONYMIZE"
    }
    pii_entities_config {
      type   = "CREDIT_DEBIT_CARD_NUMBER"
      action = "BLOCK"
    }
    pii_entities_config {
      type   = "US_SOCIAL_SECURITY_NUMBER"
      action = "BLOCK"
    }
  }

  tags = local.tags
}

resource "aws_bedrock_guardrail_version" "input" {
  guardrail_arn = aws_bedrock_guardrail.input.guardrail_arn
  description   = "released by CI"
}

# ---- Tier-2 role (orchestration + agents): model, guardrail, rerank, retrieval and state only
data "aws_iam_policy_document" "tier2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com", "ecs-tasks.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "tier2" {
  name               = "${local.name}-tier2"
  assume_role_policy = data.aws_iam_policy_document.tier2_assume.json
  tags               = local.tags
}

data "aws_iam_policy_document" "tier2" {
  statement {
    sid       = "InvokeApprovedModels"
    actions   = ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"]
    resources = concat(local.model_arns, ["arn:aws:bedrock:*::foundation-model/${var.embedding_model_id}"])
  }
  statement {
    sid       = "Guardrail"
    actions   = ["bedrock:ApplyGuardrail"]
    resources = [aws_bedrock_guardrail.input.guardrail_arn]
  }
  statement {
    sid       = "Rerank"
    actions   = ["bedrock:Rerank"]
    resources = ["*"]
  }
  statement {
    sid       = "Vectors"
    actions   = ["s3vectors:QueryVectors", "s3vectors:GetVectors", "s3vectors:PutVectors", "s3vectors:DeleteVectors", "s3vectors:ListVectors"]
    resources = ["arn:aws:s3vectors:${var.region}:${data.aws_caller_identity.this.account_id}:bucket/${local.name}-vectors/index/${var.vector_index_name}"]
  }
  statement {
    sid       = "State"
    actions   = ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem"] # rows can be deleted
    resources = [for t in aws_dynamodb_table.state : t.arn]
  }
  statement {
    sid       = "Prompts"
    actions   = ["bedrock:GetPrompt"]
    resources = ["arn:aws:bedrock:${var.region}:${data.aws_caller_identity.this.account_id}:prompt/*"]
  }
}

resource "aws_iam_role_policy" "tier2" {
  name   = "managed-ai"
  role   = aws_iam_role.tier2.id
  policy = data.aws_iam_policy_document.tier2.json
}

resource "aws_cloudwatch_log_group" "otel" {
  name              = "/e2a/${var.tenant_id}/otel"
  retention_in_days = 30
  tags              = local.tags
}

resource "aws_ecr_repository" "images" {
  for_each             = toset(["e2a-orchestration-engine", "e2a-rag-retrieval-worker", "e2a-mcp-server-worker"])
  name                 = "${local.name}/${each.value}"
  image_tag_mutability = "IMMUTABLE"
  image_scanning_configuration {
    scan_on_push = true
  }
  tags = local.tags
}
