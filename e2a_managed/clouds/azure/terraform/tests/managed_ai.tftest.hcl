mock_provider "azurerm" {}

variables {
  tenant_id = "acme"
}

run "keys_are_disabled_everywhere" {
  command = plan
  assert {
    condition = (azurerm_cognitive_account.openai.local_auth_enabled == false
      && azurerm_cognitive_account.content_safety.local_auth_enabled == false
      && azurerm_search_service.this.local_authentication_enabled == false
    && azurerm_cosmosdb_account.state.local_authentication_disabled == true)
    error_message = "Entra ID only: no service may accept keys"
  }
}

run "semantic_ranker_and_state_containers" {
  command = plan
  assert {
    condition     = azurerm_search_service.this.semantic_search_sku == "standard" && length(azurerm_cosmosdb_sql_container.state) == 2
    error_message = "hybrid+semantic search and both E2A containers are required"
  }
}
