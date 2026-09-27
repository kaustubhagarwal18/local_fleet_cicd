#!/usr/bin/env bash
# Terraform `external` data source contract: read JSON on stdin -> {"vm_name":..,"backend":..}
# write JSON on stdout -> {"ip": "..."}
set -euo pipefail
eval "$(jq -r '@sh "VM_NAME=\(.vm_name) BACKEND=\(.backend)"')"

ip=""
case "$BACKEND" in
  multipass)
    ip=$(multipass info "$VM_NAME" --format json | jq -r '.info["'"$VM_NAME"'"].ipv4[0] // empty')
    ;;
  lima)
    ip=$(limactl list "$VM_NAME" --format '{{.Address}}' 2>/dev/null | sed 's#.*://##; s#:.*##')
    ;;
  vagrant)
    ip=$(cd ".vagrant-boxes/$VM_NAME" && vagrant ssh -c "hostname -I | awk '{print \$1}'" 2>/dev/null | tr -d '\r')
    ;;
esac

# Fail rather than guess: a placeholder IP would land in the Ansible
# inventory and send SSH to the wrong machine. Terraform only shows this
# script's stderr when it exits non-zero, so a warning would go unseen.
# (`plan` never runs this: the data source waits on ensure_running, which
# changes on every run, so it is only read at apply time.)
if [ -z "$ip" ]; then
  echo "[get_ip.sh] could not resolve an IP for $VM_NAME ($BACKEND) — is the VM running? Check with: multipass info $VM_NAME" >&2
  exit 1
fi

jq -n --arg ip "$ip" '{"ip": $ip}'
