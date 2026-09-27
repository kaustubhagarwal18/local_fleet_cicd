terraform {
  required_version = ">= 1.6.0"

  required_providers {
    # No official Multipass/Lima provider exists; VM lifecycle is driven via
    # the `local` and `null` providers calling the CLI (see modules/vm).
    # This keeps state 100% local (matches the "local-only safeguards" requirement)
    # and avoids depending on a cloud provider's remote state/API.
    local = {
      source  = "hashicorp/local"
      version = "~> 2.5"
    }
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    # Used by modules/vm (data "external" "ip") to read each VM's IP.
    external = {
      source  = "hashicorp/external"
      version = "~> 2.3"
    }
  }

  # Local-only state on purpose (interview/dev context). For a real fleet,
  # swap in a `backend "s3"` / `backend "gcs"` block here — see docs/architecture.md.
  backend "local" {
    path = "terraform.tfstate"
  }
}
