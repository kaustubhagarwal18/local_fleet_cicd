#!/usr/bin/env bash
# Usage: health_check.sh <host> [port]
# Exit 0 if the node answers a completion request within budget. Defaults to
# the metrics exporter's proxy (9100), so the check exercises llama-server
# *and* the sidecar and is counted in the node's metrics; pass 8080 to hit
# llama-server directly.
set -euo pipefail
HOST="${1:?usage: health_check.sh <host> [port]}"
PORT="${2:-9100}"

resp=$(curl -sf -m 30 -X POST "http://${HOST}:${PORT}/completion" \
  -H 'Content-Type: application/json' \
  -H "X-Correlation-Id: health-check-$(date +%s)-$$" \
  -d '{"prompt":"Health check:","n_predict":8}') || {
  echo "FAIL: ${HOST}:${PORT} did not respond" >&2
  exit 1
}

if echo "$resp" | grep -q '"content"'; then
  echo "OK: ${HOST}:${PORT} healthy"
  exit 0
fi

echo "FAIL: ${HOST}:${PORT} responded but with unexpected payload: $resp" >&2
exit 1
