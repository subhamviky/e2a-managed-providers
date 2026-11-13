# Model Armor template for ModelArmorStage. Recent resource type: pin the provider and
# verify attributes before the first apply; CI validates this module non-blocking.
terraform {
  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.40"
    }
  }
}

variable "project_id" { type = string }
variable "location" {
  type    = string
  default = "us-central1"
}
variable "tenant_id" { type = string }

resource "google_model_armor_template" "e2a" {
  location    = var.location
  template_id = "e2a-${var.tenant_id}"
  filter_config {
    pi_and_jailbreak_filter_settings {
      filter_enforcement = "ENABLED"
      confidence_level   = "MEDIUM_AND_ABOVE"
    }
    malicious_uri_filter_settings {
      filter_enforcement = "ENABLED"
    }
    rai_settings {
      rai_filters {
        filter_type      = "DANGEROUS"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
      rai_filters {
        filter_type      = "HARASSMENT"
        confidence_level = "MEDIUM_AND_ABOVE"
      }
    }
    sdp_settings {
      basic_config {
        filter_enforcement = "ENABLED"
      }
    }
  }
}

output "MODEL_ARMOR_TEMPLATE" {
  value = google_model_armor_template.e2a.id
}
