# gm-database-schema-gkt.1 — cloud mode. Throwaway spike infrastructure.
#
# The provider is pinned to a minor version rather than left floating. This
# deployment exists to produce numbers a document quotes, and a provider that
# changes the default boot volume or the default security group between the run
# and a re-run would change the machine without changing this file.

terraform {
  required_version = ">= 1.9"

  required_providers {
    ibm = {
      source  = "registry.terraform.io/IBM-Cloud/ibm"
      version = "~> 1.88"
    }
  }
}

provider "ibm" {
  ibmcloud_api_key = var.ibmcloud_api_key
  region           = var.region
}
