#!/usr/bin/env bash
# Usage: rollback.sh <node-name>
set -euo pipefail
NODE="${1:?usage: rollback.sh <node-name>}"
cd "$(dirname "$0")/.."
ansible-playbook ansible/playbooks/rollback.yml -e "target_host=${NODE}"
