#!/usr/bin/env python3
"""
End-to-end smoke test: reads the live Ansible inventory, hits every node's
/completion endpoint, and asserts on latency + response shape. Complements
scripts/verify.sh (shell) with a version that's easy to run under pytest / CI
and to extend with real assertions as the fleet grows.

Usage:
    python3 tests/smoke_test.py
"""
import configparser
import json
import sys
import time
import urllib.request

INVENTORY_PATH = "ansible/inventory/hosts.ini"


def load_hosts(path=INVENTORY_PATH):
    """Return (name, ip) for every host in the inventory's [llama_fleet] group."""
    cfg = configparser.ConfigParser(allow_no_value=True, delimiters=(" ",))
    cfg.optionxform = str
    hosts = []
    with open(path) as f:
        section = None
        for line in f:
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("["):
                section = line.strip("[]")
                continue
            if section == "llama_fleet" and "ansible_host=" in line:
                name = line.split()[0]
                ip = [p for p in line.split() if p.startswith("ansible_host=")][0].split("=", 1)[1]
                hosts.append((name, ip))
    return hosts


def check_node(name, ip, port=9100, timeout=30):
    """Send one completion request to a node and check the response shape.

    Goes through the metrics-exporter proxy (port 9100) by default. Prints
    OK or FAIL with the latency or error, and returns True if the node
    answered with generated content.
    """
    req = urllib.request.Request(
        f"http://{ip}:{port}/completion",
        data=json.dumps({"prompt": "Health check:", "n_predict": 8}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    start = time.time()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read())
            elapsed = time.time() - start
            assert "content" in body, f"{name}: unexpected response shape: {body}"
            print(f"OK  {name} ({ip}) — {elapsed:.2f}s")
            return True
    except Exception as exc:  # noqa: BLE001 - smoke test, report and continue
        print(f"FAIL {name} ({ip}) — {exc}", file=sys.stderr)
        return False


def main():
    """Check every fleet node; return 0 if all are healthy, 1 otherwise."""
    hosts = load_hosts()
    if not hosts:
        print("No hosts found in inventory; has `terraform apply` run?", file=sys.stderr)
        return 1
    results = [check_node(name, ip) for name, ip in hosts]
    if not all(results):
        return 1
    print(f"All {len(hosts)} nodes healthy.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
