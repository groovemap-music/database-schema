# gm-database-schema-gkt.1 — cloud mode. Throwaway spike infrastructure.
#
# One VPC, one subnet, one security group, and one virtual server per engine on
# an identical profile. It exists for about an hour and `terraform destroy`
# removes all of it; `../run.sh --cloud-destroy` runs that and then asks IBM
# Cloud directly whether anything named with the prefix survives, because a
# clean state file is evidence about the state file and not about the account.
#
# There is no controller instance and no bastion. The benchmark driver runs ON
# each engine's instance against an engine listening on loopback, which is the
# only arrangement in which the measured numbers mean anything: several cases
# here answer in single-digit milliseconds, and a driver on a laptop in another
# country would be reporting the Atlantic rather than the search.

data "ibm_is_image" "ubuntu" {
  name = var.image_name
}

resource "ibm_is_vpc" "bench" {
  name                        = "${var.prefix}-vpc"
  resource_group              = var.resource_group_id
  address_prefix_management   = "auto"
  default_security_group_name = "${var.prefix}-default-sg"
}

resource "ibm_is_subnet" "bench" {
  name                     = "${var.prefix}-subnet"
  resource_group           = var.resource_group_id
  vpc                      = ibm_is_vpc.bench.id
  zone                     = var.zone
  total_ipv4_address_count = 16
}

# Outbound is needed and inbound is not. Each instance pulls Ubuntu packages and
# two container images and then talks to nothing; the only inbound flow is the
# SSH session that drives it.
resource "ibm_is_security_group" "bench" {
  name           = "${var.prefix}-sg"
  resource_group = var.resource_group_id
  vpc            = ibm_is_vpc.bench.id
}

resource "ibm_is_security_group_rule" "ssh_in" {
  group     = ibm_is_security_group.bench.id
  direction = "inbound"
  remote    = var.allowed_ssh_cidr
  protocol  = "tcp"
  port_min  = 22
  port_max  = 22
}

resource "ibm_is_security_group_rule" "all_out" {
  group     = ibm_is_security_group.bench.id
  direction = "outbound"
  remote    = "0.0.0.0/0"
}

resource "ibm_is_ssh_key" "bench" {
  name           = "${var.prefix}-key"
  resource_group = var.resource_group_id
  public_key     = trimspace(var.ssh_public_key)
  type           = "ed25519"
}

# cloud-init installs Docker and the handful of packages the harness needs, so
# `run.sh --cloud` can rsync the harness and start work rather than beginning
# with a package install over SSH.
resource "ibm_is_instance" "engine" {
  for_each = toset(var.engines)

  name           = "${var.prefix}-${each.key}"
  resource_group = var.resource_group_id
  image          = data.ibm_is_image.ubuntu.id
  profile        = var.profile
  vpc            = ibm_is_vpc.bench.id
  zone           = var.zone
  keys           = [ibm_is_ssh_key.bench.id]
  user_data      = file("${path.module}/cloud-init.yaml")

  primary_network_interface {
    subnet          = ibm_is_subnet.bench.id
    security_groups = [ibm_is_security_group.bench.id]
  }

  boot_volume {
    name = "${var.prefix}-${each.key}-boot"
    size = 250
  }
}

resource "ibm_is_floating_ip" "engine" {
  for_each = toset(var.engines)

  name           = "${var.prefix}-${each.key}-fip"
  resource_group = var.resource_group_id
  target         = ibm_is_instance.engine[each.key].primary_network_interface[0].id
}
