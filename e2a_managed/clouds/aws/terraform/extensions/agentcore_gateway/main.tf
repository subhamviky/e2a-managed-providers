# Bedrock AgentCore Gateway: the managed MCP endpoint in front of the services' own E2A
# MCP servers (BaseMCPServer at /mcp). Inbound: JWT from the platform's identity provider.
# Recent resource types: pin the provider and verify attributes before the first apply.
terraform {
  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 6.20"
    }
  }
}

variable "tenant_id" { type = string }
variable "gateway_role_arn" { type = string }
variable "jwt_discovery_url" { type = string }
variable "jwt_audiences" { type = list(string) }
variable "mcp_targets" {
  type        = map(string)
  description = "name -> https endpoint of an E2A MCP server (read tools only)"
}

resource "aws_bedrockagentcore_gateway" "this" {
  name            = "e2a-${var.tenant_id}-mcp"
  role_arn        = var.gateway_role_arn
  protocol_type   = "MCP"
  authorizer_type = "CUSTOM_JWT"
  authorizer_configuration {
    custom_jwt_authorizer {
      discovery_url    = var.jwt_discovery_url
      allowed_audience = var.jwt_audiences
    }
  }
}

resource "aws_bedrockagentcore_gateway_target" "mcp" {
  for_each           = var.mcp_targets
  name               = each.key
  gateway_identifier = aws_bedrockagentcore_gateway.this.gateway_id
  target_configuration {
    mcp {
      mcp_server {
        endpoint = each.value
      }
    }
  }
}

output "AGENTCORE_GATEWAY_URL" {
  value = aws_bedrockagentcore_gateway.this.gateway_url
}
