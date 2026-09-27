# VM lifecycle module. Wraps the chosen local hypervisor CLI (Multipass by
# default; Lima/Vagrant variants below) behind `null_resource` provisioners
# so the rest of the stack (Ansible, scripts) only ever deals with an IP
# address, never with the backend-specific incantations.
#
# Idempotency: `create.sh` is a no-op if a VM with this name already
# exists and is running, so re-running `terraform apply` is safe.

resource "null_resource" "vm" {
  triggers = {
    vm_name = var.vm_name
    run_id  = var.run_id
    cpus    = var.cpus
    memory  = var.memory
    disk    = var.disk
    image   = var.image
    backend = var.backend_engine
  }

  provisioner "local-exec" {
    command = "${path.module}/scripts/create.sh"
    environment = {
      VM_NAME = var.vm_name
      CPUS    = var.cpus
      MEMORY  = var.memory
      DISK    = var.disk
      IMAGE   = var.image
      BACKEND = var.backend_engine
      # Resolved relative to the cwd `terraform apply` runs from (terraform/),
      # not path.module — see scripts/ensure_ssh_key.sh for where this is generated.
      SSH_PUBLIC_KEY_PATH = var.ssh_public_key_path
    }
  }

  provisioner "local-exec" {
    when    = destroy
    command = "${path.module}/scripts/destroy.sh"
    environment = {
      VM_NAME = self.triggers.vm_name
      BACKEND = self.triggers.backend
    }
  }
}

# `null_resource.vm` only runs create.sh when first created, so a VM that
# was stopped out-of-band (reboot, `multipass stop`, host sleep) stays
# stopped and has no IP. This re-runs the idempotent create.sh on every
# apply to (re)start it. It is deliberately a separate terraform_data
# resource: re-triggering null_resource.vm would *replace* it, and
# replacement runs its destroy provisioner, which deletes the VM.
resource "terraform_data" "ensure_running" {
  triggers_replace = timestamp()

  provisioner "local-exec" {
    command = "${path.module}/scripts/create.sh"
    environment = {
      VM_NAME             = var.vm_name
      CPUS                = var.cpus
      MEMORY              = var.memory
      DISK                = var.disk
      IMAGE               = var.image
      BACKEND             = var.backend_engine
      SSH_PUBLIC_KEY_PATH = var.ssh_public_key_path
    }
  }

  depends_on = [null_resource.vm]
}

data "external" "ip" {
  program = ["${path.module}/scripts/get_ip.sh"]
  query = {
    vm_name = var.vm_name
    backend = var.backend_engine
  }
  depends_on = [terraform_data.ensure_running]
}
