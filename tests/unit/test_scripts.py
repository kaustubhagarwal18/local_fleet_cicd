"""Tests for the pipeline's shell scripts, run as subprocesses against the
fake llama-server or a throwaway inventory (no fleet needed)."""
import json
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

needs_ansible = pytest.mark.skipif(
    shutil.which("ansible-inventory") is None, reason="ansible-inventory not on PATH (run `make setup`)"
)


def run(script, *args, **env):
    """Run scripts/<script> with extra env vars; return the CompletedProcess."""
    return subprocess.run(
        ["bash", str(REPO / "scripts" / script), *args],
        capture_output=True, text=True, timeout=120,
        env={**os.environ, **env},
    )


def write_inventory(tmp_path, hosts):
    """Write an inventory shaped like terraform's hosts.ini.tpl, with ports as group vars."""
    lines = ["[llama_fleet]"] + [f"{name} ansible_host={ip}" for name, ip in hosts]
    lines += ["", "[llama_fleet:vars]", "metrics_port=9100", "llama_server_port=8080"]
    path = tmp_path / "hosts.ini"
    path.write_text("\n".join(lines) + "\n")
    return path


# --- health_check.sh ----------------------------------------------------------

def test_health_check_passes_when_the_node_generates_content(fake_llama):
    result = run("health_check.sh", "127.0.0.1", str(fake_llama.port))

    assert result.returncode == 0, result.stderr
    assert "healthy" in result.stdout
    assert fake_llama.requests[0]["path"] == "/completion"
    assert fake_llama.requests[0]["headers"]["X-Correlation-Id"].startswith("health-check-")


def test_health_check_fails_on_an_unexpected_payload(fake_llama):
    fake_llama.response = {"not": "a completion"}

    result = run("health_check.sh", "127.0.0.1", str(fake_llama.port))

    assert result.returncode == 1
    assert "unexpected payload" in result.stderr


def test_health_check_fails_when_the_node_is_down():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]

    result = run("health_check.sh", "127.0.0.1", str(closed_port))

    assert result.returncode == 1
    assert "did not respond" in result.stderr


# --- gen_prom_targets.sh ------------------------------------------------------

@needs_ansible
def test_prometheus_targets_are_rendered_from_the_inventory(tmp_path):
    inventory = write_inventory(tmp_path, [("node-a", "10.1.0.1"), ("node-b", "10.1.0.2")])
    out = tmp_path / "targets"

    result = run("gen_prom_targets.sh", INVENTORY=str(inventory), OUT_DIR=str(out))

    assert result.returncode == 0, result.stderr
    assert json.loads((out / "exporter.json").read_text()) == [
        {"targets": ["10.1.0.1:9100"], "labels": {"node": "node-a"}},
        {"targets": ["10.1.0.2:9100"], "labels": {"node": "node-b"}},
    ]
    assert [t["targets"] for t in json.loads((out / "server.json").read_text())] == [
        ["10.1.0.1:8080"], ["10.1.0.2:8080"],
    ]


@needs_ansible
def test_prometheus_targets_refuse_an_empty_inventory(tmp_path):
    inventory = write_inventory(tmp_path, [])

    result = run("gen_prom_targets.sh", INVENTORY=str(inventory), OUT_DIR=str(tmp_path / "targets"))

    assert result.returncode == 1
    assert "no llama_fleet hosts" in result.stderr


# --- verify.sh ----------------------------------------------------------------

@needs_ansible
def test_verify_fails_instead_of_passing_with_no_hosts(tmp_path):
    # Regression: an empty/unreadable inventory used to check zero nodes and
    # still print "fleet healthy" with exit 0.
    inventory = write_inventory(tmp_path, [])

    result = run("verify.sh", INVENTORY=str(inventory))

    assert result.returncode != 0
    assert "fleet healthy" not in result.stdout
