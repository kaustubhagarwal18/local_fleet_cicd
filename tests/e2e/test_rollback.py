"""Automatic rollback, tested for real (`make test-rollback`, after `make deploy`).

Stages a deliberately broken release on the first fleet node (a llama-server
that exits immediately), runs the real rolling deploy against that node, and
checks the health gate fails it and rolls the node back to the release it was
serving, leaving its rollback target (`.previous`) as it was. This changes the
live fleet for a minute, so it has its own marker and is not part of the
read-only e2e run in CI. The broken release is always removed, and
`.last_built` and `.previous` restored, afterwards.
"""
import json
import os
import subprocess
from pathlib import Path

import pytest
import yaml

from helpers import http
from smoke_test import load_hosts

REPO = Path(__file__).resolve().parents[2]
INVENTORY = REPO / "ansible" / "inventory" / "hosts.ini"
HOSTS = load_hosts(str(INVENTORY)) if INVENTORY.exists() else []
RELEASES = yaml.safe_load((REPO / "ansible/inventory/group_vars/all.yml").read_text())["llama_release_dir"]

pytestmark = [
    pytest.mark.rollback,
    pytest.mark.skipif(not HOSTS, reason="no fleet inventory — run `make deploy` first"),
]


def on_node(host, command):
    """Run a shell command as root on `host` via Ansible and return its stdout."""
    result = subprocess.run(
        ["ansible", host, "--become", "-m", "ansible.builtin.shell", "-a", command],
        cwd=REPO, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
        env={**os.environ, "ANSIBLE_STDOUT_CALLBACK": "json", "ANSIBLE_LOAD_CALLBACK_PLUGINS": "1"},
    )
    outcome = json.loads(result.stdout)["plays"][0]["tasks"][0]["hosts"][host]
    assert outcome.get("rc") == 0, f"{command!r} on {host} failed: {outcome.get('stderr') or outcome.get('msg')}"
    return outcome["stdout"].strip()


def test_a_release_that_fails_its_health_check_is_rolled_back():
    name, ip = HOSTS[0]
    serving = on_node(name, f"readlink -f {RELEASES}/current")
    last_built = on_node(name, f"cat {RELEASES}/.last_built")
    prior_previous = on_node(name, f"cat {RELEASES}/.previous 2>/dev/null || true")
    broken = f"{RELEASES}/rollback-test-broken"
    # A rollback target distinct from `serving`, so the check below can tell
    # "left as it was" from "overwritten with the restored release".
    rollback_target = f"{RELEASES}/rollback-test-older"

    on_node(name, f"mkdir -p {broken}"
                  f" && printf '#!/bin/sh\\necho broken release >&2\\nexit 1\\n' > {broken}/llama-server"
                  f" && chmod +x {broken}/llama-server"
                  f" && echo {broken} > {RELEASES}/.last_built"
                  f" && echo {rollback_target} > {RELEASES}/.previous")
    try:
        deploy = subprocess.run(
            ["ansible-playbook", "ansible/playbooks/deploy.yml", "--limit", name,
             "-e", "llama_cpp_start_timeout=20"],
            cwd=REPO, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600,
        )

        assert deploy.returncode != 0, "deploying a broken release must fail the rollout"
        assert "and was rolled back to the previous release" in deploy.stdout
        assert on_node(name, f"readlink -f {RELEASES}/current") == serving, \
            "current should point back at the release that was serving before"
        assert on_node(name, f"cat {RELEASES}/.previous") == rollback_target, \
            ".previous should still point one release back, not at the restored release"

        status, _, body = http(
            "POST", f"http://{ip}:9100/completion", {"prompt": "Still there?", "n_predict": 4},
            headers={"Content-Type": "application/json"}, timeout=60,
        )
        assert status == 200 and json.loads(body)["content"].strip(), f"{name} is not serving after rollback"
    finally:
        restore_previous = (f"echo {prior_previous} > {RELEASES}/.previous" if prior_previous
                            else f"rm -f {RELEASES}/.previous")
        on_node(name, f"echo {last_built} > {RELEASES}/.last_built && {restore_previous} && rm -rf {broken}")
