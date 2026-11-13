# Each output is the value of one profile environment variable.
output "BEDROCK_GUARDRAIL_ID" {
  value = aws_bedrock_guardrail.input.guardrail_id
}

output "BEDROCK_GUARDRAIL_VERSION" {
  value = aws_bedrock_guardrail_version.input.version
}

output "DYNAMODB_IDEMPOTENCY_TABLE" {
  value = aws_dynamodb_table.state["idempotency"].name
}

output "DYNAMODB_CHECKPOINT_TABLE" {
  value = aws_dynamodb_table.state["checkpoints"].name
}

output "TIER2_ROLE_ARN" {
  value = aws_iam_role.tier2.arn
}
