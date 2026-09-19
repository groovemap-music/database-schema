# gm-database-schema-gkt.1 — cloud mode inputs. Throwaway spike infrastructure.
#
# Every secret is read from the environment and never written to a file in this
# directory. `TF_VAR_ibmcloud_api_key` is exported by `../run.sh` from
# `~/.config/groovemap/ibmcloud.env`, which is outside the repository. There is
# no `terraform.tfvars` here and `.gitignore` refuses one, so the key cannot be
# committed by someone following the obvious habit.

variable "ibmcloud_api_key" {
  description = "IBM Cloud API key. Supplied as TF_VAR_ibmcloud_api_key; never written to a file."
  type        = string
  sensitive   = true
}

variable "region" {
  description = "IBM Cloud region."
  type        = string
  default     = "us-south"
}

variable "zone" {
  description = "Availability zone inside the region. Both instances share it, so neither pays a cross-zone hop the other does not."
  type        = string
  default     = "us-south-1"
}

variable "resource_group_id" {
  description = "Resource group the deployment is billed to."
  type        = string
}

variable "prefix" {
  description = "Name prefix, so every object this spike creates is identifiable and `terraform destroy` can be verified by name."
  type        = string
  default     = "gmgkt1"
}

variable "profile" {
  description = <<-EOT
    Instance profile. Both engines get the SAME profile: the spike compares two
    engines, and a difference in machine would be indistinguishable from a
    difference in engine.

    bx2-8x32 is 8 vCPU and 32 GiB, which holds the large catalog's hot set in
    memory on either engine. The large PostgreSQL catalog is about 6 GB of
    relations and indexes and the Neo4j store is about 5 GB, so a 32 GiB machine
    is measuring the engine rather than the page cache.
  EOT
  type        = string
  default     = "bx2-8x32"
}

variable "image_name" {
  description = "Ubuntu image. Named rather than pinned by id, because image ids are region-scoped."
  type        = string
  default     = "ibm-ubuntu-24-04-4-minimal-amd64-7"
}

variable "ssh_public_key" {
  description = "Contents of the public half of GROOVEMAP_BENCH_SSH_KEY."
  type        = string
}

variable "allowed_ssh_cidr" {
  description = <<-EOT
    The only address allowed to reach port 22. `../run.sh` sets this to the
    driving machine's own egress address, so the instances are not reachable
    from the internet at large for the hour they exist.

    Nothing else is open. The engines listen on loopback inside each instance and
    the driver runs ON the instance, so neither 5432 nor 7687 is ever exposed —
    which is also why the measured latencies are engine time and not a
    transatlantic round trip.
  EOT
  type        = string
}

variable "engines" {
  description = "One instance per engine, on the same profile."
  type        = list(string)
  default     = ["postgres", "neo4j"]
}
