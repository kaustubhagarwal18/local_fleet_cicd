#!/usr/bin/env bash
# Prints an OS-specific install command for Multipass, then fails.
# Multipass is a hard requirement the moment `terraform apply` actually
# runs (default backend_engine = "multipass" in terraform/variables.tf) —
# create.sh shells out to it directly, so a missing binary fails apply
# with a bare "command not found" deep inside a local-exec provisioner.
set -euo pipefail

echo "multipass not found. Install it:"
case "$(uname -s)" in
  Darwin)
    echo "  brew install --cask multipass"
    ;;
  Linux)
    echo "  sudo snap install multipass"
    ;;
  *)
    echo "  See https://multipass.run/install"
    ;;
esac
echo
echo "Or, if you'd rather use a different VM backend (Lima/Vagrant), set"
echo "backend_engine in terraform/variables.tf and install that tool instead —"
echo "see terraform/modules/vm/main.tf for the supported backends."
exit 1
