#!/usr/bin/env bash
# Validates the rollout.yml pipeline SHAPE via act — it does NOT create a
# real fleet. act runs each job inside a Docker container, which has no
# route to your host's Multipass/hypervisor, so under act each job swaps
# its fleet-touching steps for static checks (terraform validate/plan,
# playbook --syntax-check, bash -n) — see the note atop rollout.yml.
#
# To actually provision + deploy a real fleet, run the pipeline directly
# on your host instead of through act:
#   cd terraform && terraform apply
#   ansible-playbook ansible/playbooks/site.yml
#   ansible-playbook ansible/playbooks/build.yml
#   ansible-playbook ansible/playbooks/deploy.yml
#   bash scripts/verify.sh
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v act >/dev/null; then
  echo "act not found — install https://github.com/nektos/act" >&2
  exit 1
fi

echo "NOTE: this validates the CI pipeline shape only; it will not create real VMs."
echo "      For an actual fleet, run the steps in this script's header directly on your host."
echo

bash scripts/ensure_docker.sh

# If a fleet from `make deploy` is up, hand its inventory to the verify job
# so it runs the real e2e tests against it (act has no artifacts to pass it).
act_env=()
INVENTORY="ansible/inventory/hosts.ini"
if [ -s "$INVENTORY" ]; then
  first_ip=$(awk -F'ansible_host=' 'NF > 1 {split($2, a, " "); print a[1]; exit}' "$INVENTORY")
  if [ -n "$first_ip" ] && curl -sf -m 3 "http://${first_ip}:9100/health" >/dev/null; then
    echo "Live fleet found (${first_ip} answers): the verify job will run the e2e tests against it."
    act_env=(--env "FLEET_INVENTORY_B64=$(base64 < "$INVENTORY" | tr -d '\n')")
  else
    echo "Inventory found but the fleet isn't answering: the verify job will run static checks only."
  fi
fi

# One run executes all four jobs once, in `needs:` order. (Per-job
# `act -j` calls re-run each job's dependencies, so provision ran 4x.)
# ${act_env[@]+...}: bash 3.2 (macOS) treats an empty array as unbound under set -u.
act workflow_dispatch -W .github/workflows/rollout.yml ${act_env[@]+"${act_env[@]}"}

echo "==> Rollout complete. Run 'make verify' for a standalone smoke test."
