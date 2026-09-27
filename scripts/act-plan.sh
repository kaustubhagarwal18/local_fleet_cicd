#!/usr/bin/env bash
# Runs lint + terraform plan locally via nektos/act, exactly as CI would.
set -euo pipefail
cd "$(dirname "$0")/.."

if ! command -v act >/dev/null; then
  echo "act not found — install https://github.com/nektos/act" >&2
  exit 1
fi

bash scripts/ensure_docker.sh

act -W .github/workflows/ci-lint.yml -j lint
act -W .github/workflows/plan.yml -j plan --secret-file .secrets 2>/dev/null || \
  act -W .github/workflows/plan.yml -j plan
