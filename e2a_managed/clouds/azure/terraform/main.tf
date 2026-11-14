# Managed-AI substrate for E2A services on Azure (openai_azure profile): Azure OpenAI
# (Foundry) deployments, AI Search, AI Content Safety, Cosmos DB state, Application
# Insights, one user-assigned identity per tier with data-plane RBAC (no keys).
terraform {
  required_version = ">= 1.7"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
}

locals {
  name = "e2a-${var.tenant_id}"
  tags = { "e2a-tenant" = var.tenant_id, "e2a-tenancy" = var.tenancy_model }
}

resource "azurerm_resource_group" "this" {
  name     = "rg-${local.name}"
  location = var.location
  tags     = local.tags
}

resource "azurerm_user_assigned_identity" "tier" {
  for_each            = toset(["tier1", "tier2", "tier4"])
  name                = "id-${local.name}-${each.value}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
}

# ---- Azure OpenAI: chat (Responses API) + embeddings deployments, Entra-only auth
resource "azurerm_cognitive_account" "openai" {
  name                  = "oai-${local.name}"
  location              = azurerm_resource_group.this.location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "OpenAI"
  sku_name              = "S0"
  custom_subdomain_name = "oai-${local.name}"
  local_auth_enabled    = false
  tags                  = local.tags
}

resource "azurerm_cognitive_deployment" "chat" {
  name                 = var.chat_deployment
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = var.chat_model
    version = var.chat_model_version
  }
  sku {
    name     = "GlobalStandard"
    capacity = var.chat_capacity_k_tpm
  }
}

resource "azurerm_cognitive_deployment" "embedding" {
  name                 = var.embedding_deployment
  cognitive_account_id = azurerm_cognitive_account.openai.id
  model {
    format  = "OpenAI"
    name    = "text-embedding-3-large"
    version = "1"
  }
  sku {
    name     = "Standard"
    capacity = 120
  }
}

# ---- AI Content Safety (Prompt Shields + harm categories)
resource "azurerm_cognitive_account" "content_safety" {
  name                  = "cs-${local.name}"
  location              = azurerm_resource_group.this.location
  resource_group_name   = azurerm_resource_group.this.name
  kind                  = "ContentSafety"
  sku_name              = "S0"
  custom_subdomain_name = "cs-${local.name}"
  local_auth_enabled    = false
  tags                  = local.tags
}

# ---- AI Search: hybrid + semantic ranker (index schema is created by the ingestion job)
resource "azurerm_search_service" "this" {
  name                          = "srch-${local.name}"
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  sku                           = "standard"
  semantic_search_sku           = "standard"
  local_authentication_enabled  = false
  public_network_access_enabled = false
  replica_count                 = var.zone_redundant ? 2 : 1
  tags                          = local.tags
}

# ---- Cosmos DB: E2A claims and checkpoints (ETag conditional writes), Entra-only
resource "azurerm_cosmosdb_account" "state" {
  name                          = "cosmos-${local.name}"
  location                      = azurerm_resource_group.this.location
  resource_group_name           = azurerm_resource_group.this.name
  offer_type                    = "Standard"
  kind                          = "GlobalDocumentDB"
  local_authentication_disabled = true
  consistency_policy {
    consistency_level = "Strong"
  }
  geo_location {
    location          = azurerm_resource_group.this.location
    failover_priority = 0
    zone_redundant    = var.zone_redundant
  }
  tags = local.tags
}

resource "azurerm_cosmosdb_sql_database" "e2a" {
  name                = var.tenancy_model == "SILO" ? "e2a-${var.tenant_id}" : "e2a"
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.state.name
}

# one container for every E2A record (claims, checkpoints, rows, leases): e2a-adapters' "azure"
# profile (COSMOS_CONTAINER); the record kind is part of each item's key
resource "azurerm_cosmosdb_sql_container" "state" {
  for_each            = toset(["e2a"])
  name                = each.value
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.state.name
  database_name       = azurerm_cosmosdb_sql_database.e2a.name
  partition_key_paths = ["/pk"]
  default_ttl         = -1
}

# ---- Telemetry: Application Insights (OpenTelemetry via azure-monitor-opentelemetry)
resource "azurerm_log_analytics_workspace" "this" {
  name                = "log-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  retention_in_days   = 30
}

resource "azurerm_application_insights" "this" {
  name                = "appi-${local.name}"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  workspace_id        = azurerm_log_analytics_workspace.this.id
  application_type    = "other"
}

# ---- Data-plane RBAC for Tier 2 (no keys anywhere)
resource "azurerm_role_assignment" "tier2" {
  for_each = {
    openai  = { scope = azurerm_cognitive_account.openai.id, role = "Cognitive Services OpenAI User" }
    safety  = { scope = azurerm_cognitive_account.content_safety.id, role = "Cognitive Services User" }
    search  = { scope = azurerm_search_service.this.id, role = "Search Index Data Reader" }
    monitor = { scope = azurerm_application_insights.this.id, role = "Monitoring Metrics Publisher" }
  }
  scope                = each.value.scope
  role_definition_name = each.value.role
  principal_id         = azurerm_user_assigned_identity.tier["tier2"].principal_id
}

resource "azurerm_role_assignment" "tier4_search_writer" {
  scope                = azurerm_search_service.this.id
  role_definition_name = "Search Index Data Contributor"
  principal_id         = azurerm_user_assigned_identity.tier["tier4"].principal_id
}

resource "azurerm_cosmosdb_sql_role_assignment" "tier2_state" {
  resource_group_name = azurerm_resource_group.this.name
  account_name        = azurerm_cosmosdb_account.state.name
  role_definition_id  = "${azurerm_cosmosdb_account.state.id}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002"
  principal_id        = azurerm_user_assigned_identity.tier["tier2"].principal_id
  scope               = azurerm_cosmosdb_account.state.id
}
