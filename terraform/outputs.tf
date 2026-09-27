output "fleet" {
  description = "Map of node name -> IP address"
  value       = { for k, v in module.vm : k => v.ip_address }
}

output "inventory_path" {
  value = local_file.ansible_inventory.filename
}
