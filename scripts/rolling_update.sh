#!/usr/bin/env bash
# Sequential, health-gated rolling update across the fleet, mirroring what
# ansible/playbooks/deploy.yml does via `serial: 1`. Useful for triggering a
# rollout outside of `act`/CI, e.g. `./scripts/rolling_update.sh`.
set -euo pipefail
cd "$(dirname "$0")/.."

INVENTORY="ansible/inventory/hosts.ini"
nodes=$(ansible-inventory -i "$INVENTORY" --list | jq -r '.llama_fleet.hosts[]')

for node in $nodes; do
  echo "==> Deploying to $node"
  ansible-playbook ansible/playbooks/deploy.yml --limit "$node"

  ip=$(ansible-inventory -i "$INVENTORY" --host "$node" | jq -r '.ansible_host')
  if ! bash scripts/health_check.sh "$ip"; then
    echo "!! Health check failed for $node — rolling back" >&2
    ansible-playbook ansible/playbooks/rollback.yml -e "target_host=$node"
    echo "!! Aborting rollout; $node and later nodes left on previous version" >&2
    exit 1
  fi
  echo "==> $node healthy, proceeding"
done

echo "==> Rolling update complete across all nodes"
