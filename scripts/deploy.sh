#!/usr/bin/env bash
# Runs the REAL end-to-end pipeline directly on this host: provisions the
# fleet with Terraform, builds llama.cpp + stages the model, performs the
# health-gated rolling deploy, then verifies. This is the counterpart to
# `make act-rollout`, which validates the same pipeline's shape/logic
# inside act's Docker containers but cannot create real VMs (containers
# have no route to the host's Multipass/hypervisor — see
# docs/troubleshooting.md). Use this script to actually stand up the fleet.
set -euo pipefail
cd "$(dirname "$0")/.."

command -v multipass >/dev/null || bash scripts/hint_multipass.sh
command -v ansible-playbook >/dev/null || { echo "ansible not found — see README.md#prerequisites" >&2; exit 1; }
# Checked up front so a missing pytest can't fail the run after the fleet is built.
command -v pytest >/dev/null || { echo "pytest not found — run 'make setup' (or use 'make deploy', which uses .venv)" >&2; exit 1; }

# Pick ONE of terraform/tofu and stick with it — trying both via `||` masks
# the real error behind a second, unrelated one if the first genuinely fails.
if command -v terraform >/dev/null; then
  TF_BIN=terraform
elif command -v tofu >/dev/null; then
  TF_BIN=tofu
else
  bash scripts/hint_terraform.sh
fi

echo "==> [0/6] ensuring fleet SSH key exists"
bash scripts/ensure_ssh_key.sh

echo "==> [1/6] terraform init + apply (provisioning fleet)"
(cd terraform && "$TF_BIN" init && "$TF_BIN" apply -auto-approve)

# site.yml installs the toolchain (cmake, build-essential) and the metrics
# exporter that build.yml and deploy.yml depend on — it must run first.
echo "==> [2/6] ansible: base config (common + observability)"
ansible-playbook ansible/playbooks/site.yml

echo "==> [3/6] ansible: build llama.cpp + stage model"
ansible-playbook ansible/playbooks/build.yml

echo "==> [4/6] ansible: rolling deploy (serial:1, health-gated, auto-rollback)"
ansible-playbook ansible/playbooks/deploy.yml

echo "==> [5/6] verify: smoke test on every node"
bash scripts/verify.sh

# The same e2e suite the CI rollout runs: text generation, correlation IDs,
# real metrics values, and llama-server's own /health and /metrics.
echo "==> [6/6] e2e tests against the live fleet"
pytest -m e2e

echo "==> Deploy complete. Fleet is up and healthy."
