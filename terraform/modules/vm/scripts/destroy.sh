#!/usr/bin/env bash
set -euo pipefail
: "${VM_NAME:?}" "${BACKEND:?}"

case "$BACKEND" in
  multipass)
    multipass delete "$VM_NAME" --purge >/dev/null 2>&1 || true
    ;;
  lima)
    limactl stop "$VM_NAME" >/dev/null 2>&1 || true
    limactl delete "$VM_NAME" >/dev/null 2>&1 || true
    ;;
  vagrant)
    (cd ".vagrant-boxes/$VM_NAME" && vagrant destroy -f) || true
    rm -rf ".vagrant-boxes/$VM_NAME"
    ;;
esac
echo "[destroy.sh] $VM_NAME removed"
