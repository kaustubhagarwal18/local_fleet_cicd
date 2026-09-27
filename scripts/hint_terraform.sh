#!/usr/bin/env bash
# Prints an OS-specific install command for terraform/tofu, then fails —
# there's no single safe way to auto-install a binary across package
# managers, so unlike Docker (a daemon we can launch) this just points
# you at the right command instead of guessing.
set -euo pipefail

echo "terraform/tofu not found. Install one of:"
case "$(uname -s)" in
  Darwin)
    echo "  brew install hashicorp/tap/terraform"
    echo "  # or: brew install opentofu"
    ;;
  Linux)
    echo "  # Terraform (Debian/Ubuntu):"
    echo "  wget -O- https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg"
    echo "  echo \"deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com \$(lsb_release -cs) main\" | sudo tee /etc/apt/sources.list.d/hashicorp.list"
    echo "  sudo apt update && sudo apt install terraform"
    echo "  # or OpenTofu (no root needed):"
    echo "  curl -fsSL https://get.opentofu.org/install-opentofu.sh -o install-opentofu.sh && chmod +x install-opentofu.sh && ./install-opentofu.sh --install-method standalone"
    ;;
  *)
    echo "  See https://developer.hashicorp.com/terraform/install or https://opentofu.org/docs/intro/install/"
    ;;
esac
exit 1
