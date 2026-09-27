#!/usr/bin/env bash
# Idempotent VM create for the configured backend.
#
# SSH access: only the "multipass" branch below injects the fleet SSH key
# (via cloud-init) and targets the "ubuntu" user Ansible expects. The
# lima/vagrant branches are provided as swappable alternatives per the
# assignment's tool flexibility, but are unverified end-to-end in this
# repo (this project was built and tested against Multipass) — wiring
# equivalent SSH key injection and confirming each backend's actual
# default login user (Vagrant's `ubuntu/jammy64` box defaults to "vagrant",
# not "ubuntu") is required before either is production-ready here.
set -euo pipefail

: "${VM_NAME:?}" "${BACKEND:?}"
CPUS="${CPUS:-2}"
MEMORY="${MEMORY:-2G}"
DISK="${DISK:-10G}"
IMAGE="${IMAGE:-22.04}"

case "$BACKEND" in
  multipass)
    if multipass info "$VM_NAME" >/dev/null 2>&1; then
      echo "[create.sh] $VM_NAME already exists, ensuring it's running"
      # A no-op if it's already running; a real failure must stop the apply.
      multipass start "$VM_NAME" >/dev/null
    else
      : "${SSH_PUBLIC_KEY_PATH:?SSH_PUBLIC_KEY_PATH not set — run scripts/ensure_ssh_key.sh first}"
      if [ ! -f "$SSH_PUBLIC_KEY_PATH" ]; then
        echo "[create.sh] $SSH_PUBLIC_KEY_PATH not found — run scripts/ensure_ssh_key.sh (or 'make deploy', which does this for you)" >&2
        exit 1
      fi
      # Multipass does not expose a key usable by plain ssh/Ansible by default
      # (it manages its own internal keypair for `multipass shell`/`exec`).
      # cloud-init's top-level `ssh_authorized_keys` merges safely into the
      # image's default "ubuntu" user without disturbing Multipass's own
      # access — see docs/architecture.md for the full explanation.
      CLOUD_INIT_FILE=$(mktemp)
      trap 'rm -f "$CLOUD_INIT_FILE"' EXIT
      {
        echo "#cloud-config"
        echo "ssh_authorized_keys:"
        echo "  - \"$(cat "$SSH_PUBLIC_KEY_PATH")\""
      } > "$CLOUD_INIT_FILE"
      multipass launch "$IMAGE" --name "$VM_NAME" --cpus "$CPUS" --memory "$MEMORY" --disk "$DISK" --cloud-init "$CLOUD_INIT_FILE"
    fi
    ;;
  lima)
    if limactl list --format '{{.Name}}' | grep -qx "$VM_NAME"; then
      limactl start "$VM_NAME" >/dev/null 2>&1 || true
    else
      limactl start --name="$VM_NAME" --cpus="$CPUS" --memory="${MEMORY%G}" --disk="${DISK%G}" template://ubuntu-lts
    fi
    ;;
  vagrant)
    mkdir -p ".vagrant-boxes/$VM_NAME"
    cat > ".vagrant-boxes/$VM_NAME/Vagrantfile" <<VAGRANT
Vagrant.configure("2") do |c|
  c.vm.box = "ubuntu/jammy64"
  c.vm.hostname = "$VM_NAME"
  c.vm.provider "virtualbox" do |v|
    v.cpus = $CPUS
    v.memory = "${MEMORY%G}000"
  end
end
VAGRANT
    (cd ".vagrant-boxes/$VM_NAME" && vagrant up)
    ;;
  *)
    echo "Unsupported backend: $BACKEND" >&2
    exit 1
    ;;
esac
echo "[create.sh] $VM_NAME ready"
