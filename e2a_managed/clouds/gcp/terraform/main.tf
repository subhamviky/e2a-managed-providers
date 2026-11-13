# Managed-AI substrate for E2A services on Google Cloud (google_vertex profile): APIs,
# one service account per tier, Vertex AI Vector Search (streaming, hybrid-capable),
# Firestore for E2A state, and IAM. Gemini and the Ranking API need no resources, only
# the APIs and roles. Model Armor is in extensions/ (recent resource type).
terraform {
  required_version = ">= 1.7"
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

locals {
  name = "e2a-${var.tenant_id}"
  apis = ["aiplatform.googleapis.com", "discoveryengine.googleapis.com", "modelarmor.googleapis.com",
  "firestore.googleapis.com", "run.googleapis.com", "cloudtrace.googleapis.com", "logging.googleapis.com"]
}

resource "google_project_service" "apis" {
  for_each           = toset(local.apis)
  service            = each.value
  disable_on_destroy = false
}

resource "google_service_account" "tier" {
  for_each     = toset(["tier1", "tier2", "tier4"])
  account_id   = "${local.name}-${each.value}"
  display_name = "E2A ${each.value} (${var.tenant_id})"
}

resource "google_project_iam_member" "tier2" {
  for_each = toset(["roles/aiplatform.user", "roles/modelarmor.user", "roles/discoveryengine.viewer",
  "roles/datastore.user", "roles/cloudtrace.agent", "roles/logging.logWriter"])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.tier["tier2"].email}"
}

resource "google_project_iam_member" "tier4" {
  for_each = toset(["roles/aiplatform.user", "roles/datastore.user"])
  project  = var.project_id
  role     = each.value
  member   = "serviceAccount:${google_service_account.tier["tier4"].email}"
}

# ---- Vertex AI Vector Search: streaming updates, tree-AH, cosine (dense; sparse added per datapoint)
resource "google_vertex_ai_index" "knowledge" {
  display_name        = "${local.name}-${var.vector_index_name}"
  region              = var.region
  index_update_method = "STREAM_UPDATE"
  metadata {
    config {
      dimensions                  = var.embedding_dimensions
      approximate_neighbors_count = 50
      distance_measure_type       = "COSINE_DISTANCE"
      feature_norm_type           = "UNIT_L2_NORM"
      algorithm_config {
        tree_ah_config {
          leaf_node_embedding_count = 1000
        }
      }
    }
  }
  depends_on = [google_project_service.apis]
}

resource "google_vertex_ai_index_endpoint" "knowledge" {
  display_name            = "${local.name}-endpoint"
  region                  = var.region
  public_endpoint_enabled = true
}

resource "google_vertex_ai_index_endpoint_deployed_index" "knowledge" {
  index_endpoint    = google_vertex_ai_index_endpoint.knowledge.id
  index             = google_vertex_ai_index.knowledge.id
  deployed_index_id = replace("${local.name}_knowledge", "-", "_")
}

# ---- Firestore (native mode): E2A claims and checkpoints
resource "google_firestore_database" "state" {
  name        = var.tenancy_model == "SILO" ? "${local.name}-state" : "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"
  depends_on  = [google_project_service.apis]
}
