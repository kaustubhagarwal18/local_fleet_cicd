#!/usr/bin/env bash
# Renders Prometheus file_sd targets from the live Ansible inventory, so the
# scrape config always matches the fleet Terraform actually provisioned
# (node count, IPs, and ports from group_vars). Used by `make observability`.
set -euo pipefail
cd "$(dirname "$0")/.."

# Both overridable (tests point them at a temp inventory/dir).
INVENTORY="${INVENTORY:-ansible/inventory/hosts.ini}"
OUT_DIR="${OUT_DIR:-observability/prometheus/targets}"

inventory=$(ansible-inventory -i "$INVENTORY" --list)
if [ -z "$(jq -r '.llama_fleet.hosts[]?' <<<"$inventory")" ]; then
  echo "gen_prom_targets.sh: no llama_fleet hosts in $INVENTORY — run 'make deploy' first" >&2
  exit 1
fi

mkdir -p "$OUT_DIR"
# One file per scrape job; the port comes from each host's vars.
render() {
  jq --arg port_var "$1" '. as $inv | [
    $inv.llama_fleet.hosts[] as $h | $inv._meta.hostvars[$h] as $v |
    {targets: ["\($v.ansible_host):\($v[$port_var])"], labels: {node: $h}}
  ]' <<<"$inventory"
}
render metrics_port > "$OUT_DIR/exporter.json"
render llama_server_port > "$OUT_DIR/server.json"

echo "gen_prom_targets.sh: wrote $(jq length "$OUT_DIR/exporter.json") node(s) to $OUT_DIR/"
