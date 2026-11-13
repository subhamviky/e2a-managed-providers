output "VERTEX_INDEX" {
  value = google_vertex_ai_index.knowledge.id
}

output "VERTEX_INDEX_ENDPOINT" {
  value = google_vertex_ai_index_endpoint.knowledge.id
}

output "VERTEX_DEPLOYED_INDEX_ID" {
  value = google_vertex_ai_index_endpoint_deployed_index.knowledge.deployed_index_id
}

output "FIRESTORE_DATABASE" {
  value = google_firestore_database.state.name
}

output "TIER2_SERVICE_ACCOUNT" {
  value = google_service_account.tier["tier2"].email
}
