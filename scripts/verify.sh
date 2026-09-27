#!/usr/bin/env bash
# End-to-end smoke test: hits /completion on every fleet node and checks
# metrics are being exposed. Used by `make verify` and the rollout.yml
# `verify` job.
set -euo pipefail
cd "$(dirname "$0")/.."
INVENTORY="${INVENTORY:-ansible/inventory/hosts.ini}"  # overridable for tests

# Resolve hosts up front: inside `for ... in $(...)` a failing
# ansible-inventory is ignored by set -e, and zero hosts would "pass".
hosts=$(ansible-inventory -i "$INVENTORY" --list | jq -r '.llama_fleet.hosts[]')
if [ -z "$hosts" ]; then
  echo "verify.sh: no llama_fleet hosts found in $INVENTORY" >&2
  exit 1
fi

fail=0
for host in $hosts; do
  ip=$(ansible-inventory -i "$INVENTORY" --host "$host" | jq -r '.ansible_host')
  echo "-- $host ($ip) --"
  bash scripts/health_check.sh "$ip" || fail=1
  # The health check above went through the exporter, so it must now report
  # real throughput, not just expose metric names.
  metrics=$(curl -sf -m 5 "http://${ip}:9100/metrics") || metrics=""
  tps=$(awk '$1 == "llama_tokens_per_second" {print $2}' <<<"$metrics")
  load=$(awk '$1 == "llama_model_load_time_seconds" {print $2}' <<<"$metrics")
  if awk -v t="${tps:-0}" 'BEGIN {exit !(t > 0)}'; then
    echo "OK: metrics exposed (tokens/sec ${tps}, model load ${load:-n/a}s)"
  else
    echo "FAIL: ${ip}:9100/metrics missing or reports no throughput"
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "verify.sh: one or more nodes failed verification" >&2
  exit 1
fi
echo "verify.sh: fleet healthy"
