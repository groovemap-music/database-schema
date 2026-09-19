# gm-database-schema-gkt.1 — cloud mode outputs. Throwaway spike infrastructure.
#
# `../run.sh` reads these with `terraform output -json`. None of them is
# sensitive; the API key is an input and never an output.

output "instances" {
  description = "Engine name to floating IP, which is how run.sh reaches each instance."
  value       = { for name in var.engines : name => ibm_is_floating_ip.engine[name].address }
}

output "profile" {
  description = "Recorded in the spike document's Method section beside every cloud number."
  value       = var.profile
}

output "zone" {
  value = var.zone
}

output "image" {
  value = data.ibm_is_image.ubuntu.name
}

output "instance_ids" {
  description = "For the post-destroy check, which asks IBM Cloud rather than the state file."
  value       = { for name in var.engines : name => ibm_is_instance.engine[name].id }
}
