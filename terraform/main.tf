resource "random_id" "run" {
  byte_length = 3
}

module "vm" {
  source = "./modules/vm"

  for_each = { for i in range(var.vm_count) : "${var.vm_name_prefix}-${i + 1}" => i }

  vm_name             = each.key
  cpus                = var.vm_cpus
  memory              = var.vm_memory
  disk                = var.vm_disk
  image               = var.vm_image
  backend_engine      = var.backend_engine
  run_id              = random_id.run.hex
  ssh_public_key_path = var.ssh_public_key_path
}

# Renders the Ansible inventory from the actual provisioned VM IPs, so
# Ansible always targets what Terraform actually created (no drift).
# ansible_ssh_private_key_file needs a path resolvable from the repo root
# (where ansible-playbook/ansible.cfg live), not from terraform/ — hence
# the "terraform/" prefix here, distinct from ssh_public_key_path above
# which the vm module resolves relative to its own working directory.
resource "local_file" "ansible_inventory" {
  filename = "${path.module}/../ansible/inventory/hosts.ini"
  content = templatefile("${path.module}/../ansible/inventory/hosts.ini.tpl", {
    nodes                = { for k, v in module.vm : k => v.ip_address }
    ssh_private_key_path = "terraform/${var.ssh_private_key_path}"
  })
}
