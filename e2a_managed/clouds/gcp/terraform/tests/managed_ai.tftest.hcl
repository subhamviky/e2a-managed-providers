mock_provider "google" {}

variables {
  tenant_id  = "acme"
  project_id = "e2a-test"
}

run "vector_search_streams_and_matches_the_embedding_size" {
  command = plan
  assert {
    condition     = google_vertex_ai_index.knowledge.index_update_method == "STREAM_UPDATE" && google_vertex_ai_index.knowledge.metadata[0].config[0].dimensions == 768
    error_message = "streaming index sized to the profile's embedding_dimensions is required"
  }
}

run "tier2_can_use_vertex_and_model_armor" {
  command = plan
  assert {
    condition     = contains(keys(google_project_iam_member.tier2), "roles/aiplatform.user") && contains(keys(google_project_iam_member.tier2), "roles/modelarmor.user")
    error_message = "tier 2 needs Vertex AI and Model Armor"
  }
}
