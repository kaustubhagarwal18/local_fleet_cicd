# Plan-only assertions, run with `terraform test` (`make test`, and the lint
# workflow in CI/act). `command = plan` never runs provisioners or apply-time
# data sources, so no VM is created and no hypervisor is needed.
#
# vm_count is set in every run because terraform test also reads a local
# terraform.tfvars, which may override the default.

run "three_node_fleet" {
  command = plan

  variables {
    vm_count = 3
  }

  assert {
    condition     = toset(keys(module.vm)) == toset(["llama-node-1", "llama-node-2", "llama-node-3"])
    error_message = "A 3-node fleet should be named llama-node-1..3."
  }

  assert {
    condition     = local_file.ansible_inventory.filename == "./../ansible/inventory/hosts.ini"
    error_message = "The inventory must be rendered to ansible/inventory/hosts.ini, where ansible.cfg looks for it."
  }
}

run "two_node_fleet_with_custom_prefix" {
  command = plan

  variables {
    vm_count       = 2
    vm_name_prefix = "edge"
  }

  assert {
    condition     = toset(keys(module.vm)) == toset(["edge-1", "edge-2"])
    error_message = "vm_count and vm_name_prefix should drive the node names."
  }
}

run "rejects_fleet_smaller_than_two" {
  command = plan

  variables {
    vm_count = 1
  }

  expect_failures = [var.vm_count]
}

run "rejects_fleet_larger_than_three" {
  command = plan

  variables {
    vm_count = 4
  }

  expect_failures = [var.vm_count]
}

run "rejects_unknown_backend" {
  command = plan

  variables {
    vm_count       = 2
    backend_engine = "virtualbox"
  }

  expect_failures = [var.backend_engine]
}

# The vm module on its own: sizing must reach create.sh through the
# null_resource triggers (a change there is what forces VM re-creation).
run "vm_module_passes_sizing_to_create_script" {
  command = plan

  module {
    source = "./modules/vm"
  }

  variables {
    vm_name             = "test-node"
    cpus                = 2
    memory              = "2G"
    disk                = "10G"
    image               = "22.04"
    backend_engine      = "multipass"
    run_id              = "abc123"
    ssh_public_key_path = "modules/vm/ssh/fleet_ed25519.pub"
  }

  assert {
    condition = (
      null_resource.vm.triggers.vm_name == "test-node" &&
      null_resource.vm.triggers.cpus == "2" &&
      null_resource.vm.triggers.memory == "2G" &&
      null_resource.vm.triggers.disk == "10G" &&
      null_resource.vm.triggers.backend == "multipass"
    )
    error_message = "VM name, sizing and backend must all be recorded in the null_resource triggers."
  }
}
