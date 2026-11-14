# Offline: `terraform test` with a mock AWS provider. No account, no network.
mock_provider "aws" {
  mock_data "aws_caller_identity" {
    defaults = { account_id = "123456789012" }
  }
  mock_data "aws_iam_policy_document" {
    defaults = { json = "{\"Version\":\"2012-10-17\",\"Statement\":[]}" }
  }
}

variables {
  tenant_id = "acme"
  model_ids = ["anthropic.claude-opus-5-5", "anthropic.claude-opus-4-8"]
}

run "guardrail_blocks_prompt_attacks" {
  command = plan
  assert {
    condition     = contains([for f in aws_bedrock_guardrail.input.content_policy_config[0].filters_config : f.type], "PROMPT_ATTACK")
    error_message = "the ingress guardrail must filter prompt attacks"
  }
}

run "state_tables_are_conditional_write_ready" {
  command = plan
  assert {
    condition     = aws_dynamodb_table.state["idempotency"].hash_key == "pk" && aws_dynamodb_table.state["idempotency"].range_key == "sk" && aws_dynamodb_table.state["checkpoints"].range_key == "sk" && aws_dynamodb_table.state["checkpoints"].billing_mode == "PAY_PER_REQUEST"
    error_message = "E2A stores need pk-keyed on-demand tables"
  }
}

run "tenancy_model_is_validated" {
  command = plan
  variables {
    tenancy_model = "SHARED"
  }
  expect_failures = [var.tenancy_model]
}
