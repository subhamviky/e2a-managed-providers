output "AZURE_OPENAI_ENDPOINT" {
  value = azurerm_cognitive_account.openai.endpoint
}

output "AZURE_SEARCH_ENDPOINT" {
  value = "https://${azurerm_search_service.this.name}.search.windows.net"
}

output "AZURE_CONTENT_SAFETY_ENDPOINT" {
  value = azurerm_cognitive_account.content_safety.endpoint
}

output "COSMOS_ENDPOINT" {
  value = azurerm_cosmosdb_account.state.endpoint
}

output "APPLICATIONINSIGHTS_CONNECTION_STRING" {
  value     = azurerm_application_insights.this.connection_string
  sensitive = true
}

output "COSMOS_DATABASE" {
  value = azurerm_cosmosdb_sql_database.e2a.name
}

output "COSMOS_CONTAINER" {
  value = azurerm_cosmosdb_sql_container.state["e2a"].name
}
