[llama_fleet]
%{ for name, ip in nodes ~}
${name} ansible_host=${ip} ansible_user=ubuntu
%{ endfor ~}

[llama_fleet:vars]
ansible_ssh_common_args='-o StrictHostKeyChecking=no'
ansible_ssh_private_key_file=${ssh_private_key_path}
