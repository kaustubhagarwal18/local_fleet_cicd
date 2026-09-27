#!/usr/bin/env bash
# Generates a project-local SSH keypair (once, idempotently) used to reach
# the fleet nodes. Multipass does NOT expose a key usable by plain `ssh`/
# Ansible by default — it manages its own internal keypair for `multipass
# shell`/`exec`, separate from anything in ~/.ssh. So we generate our own,
# inject its public half into each VM via cloud-init at launch (see
# terraform/modules/vm/scripts/create.sh), and point Ansible at the private
# half via ansible/inventory/hosts.ini.tpl. Never committed — see .gitignore.
set -euo pipefail
cd "$(dirname "$0")/.."

KEY_DIR="terraform/modules/vm/ssh"
KEY_PATH="$KEY_DIR/fleet_ed25519"

mkdir -p "$KEY_DIR"

if [ -f "$KEY_PATH" ]; then
  echo "[ensure_ssh_key.sh] fleet SSH key already exists, leaving it in place"
  exit 0
fi

ssh-keygen -t ed25519 -N "" -f "$KEY_PATH" -C "devops-local-fleet-cicd" >/dev/null
chmod 600 "$KEY_PATH"
chmod 644 "$KEY_PATH.pub"
echo "[ensure_ssh_key.sh] generated new fleet SSH keypair at $KEY_PATH"
