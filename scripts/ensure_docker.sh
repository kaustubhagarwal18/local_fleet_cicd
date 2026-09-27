#!/usr/bin/env bash
# Ensures a Docker daemon is reachable before anything that needs it
# (act, molecule) runs. On macOS, will attempt to launch Docker Desktop
# and poll until it's ready. On Linux, gives the right systemctl command
# instead of guessing, since start mechanisms vary by distro/init system.
set -euo pipefail

if docker info >/dev/null 2>&1; then
  exit 0
fi

echo "Docker daemon not reachable."

case "$(uname -s)" in
  Darwin)
    if [ -d "/Applications/Docker.app" ]; then
      echo "Attempting to start Docker Desktop..."
      open -a Docker
      echo -n "Waiting for Docker daemon"
      for _ in $(seq 1 30); do
        if docker info >/dev/null 2>&1; then
          echo " — ready."
          exit 0
        fi
        echo -n "."
        sleep 2
      done
      echo
      echo "ERROR: Docker Desktop did not become ready within 60s. Open it manually and re-run." >&2
      exit 1
    else
      echo "ERROR: Docker Desktop not found at /Applications/Docker.app." >&2
      echo "Install it: https://www.docker.com/products/docker-desktop/  (or: brew install --cask docker)" >&2
      exit 1
    fi
    ;;
  Linux)
    echo "ERROR: Docker daemon not running." >&2
    echo "Start it with your distro's init system, e.g.:" >&2
    echo "  sudo systemctl start docker" >&2
    echo "Then verify with: docker info" >&2
    exit 1
    ;;
  *)
    echo "ERROR: Unrecognized OS; start Docker manually and re-run." >&2
    exit 1
    ;;
esac
