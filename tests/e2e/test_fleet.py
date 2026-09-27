"""End-to-end checks against the live fleet (`make test-e2e`, after `make deploy`).

Every node in ansible/inventory/hosts.ini must serve completions through the
metrics-exporter proxy, report real metrics, and expose llama-server's own
health and metrics endpoints. Skipped when there is no inventory.
"""
import json
import uuid
from pathlib import Path

import pytest

from helpers import http, parse_metrics
from smoke_test import load_hosts

INVENTORY = Path(__file__).resolve().parents[2] / "ansible" / "inventory" / "hosts.ini"
HOSTS = load_hosts(str(INVENTORY)) if INVENTORY.exists() else []
EXPORTER_PORT, SERVER_PORT = 9100, 8080

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(not HOSTS, reason="no fleet inventory — run `make deploy` first"),
]
each_node = pytest.mark.parametrize("name, ip", HOSTS, ids=[name for name, _ in HOSTS])


@each_node
def test_node_generates_text_through_the_proxy(name, ip):
    correlation_id = f"e2e-{uuid.uuid4()}"
    status, headers, body = http(
        "POST", f"http://{ip}:{EXPORTER_PORT}/completion",
        {"prompt": "The capital of France is", "n_predict": 8},
        headers={"Content-Type": "application/json", "X-Correlation-Id": correlation_id},
        timeout=60,
    )

    assert status == 200, f"{name}: {body[:200]!r}"
    assert json.loads(body)["content"].strip(), f"{name} returned no text"
    assert headers["X-Correlation-Id"] == correlation_id


@each_node
def test_node_reports_real_inference_metrics(name, ip):
    http("POST", f"http://{ip}:{EXPORTER_PORT}/completion", {"prompt": "Hi", "n_predict": 4},
         headers={"Content-Type": "application/json"}, timeout=60)

    status, _, body = http("GET", f"http://{ip}:{EXPORTER_PORT}/metrics")
    m = parse_metrics(body.decode())

    assert status == 200
    assert m["llama_requests_total"] >= 1
    assert m["llama_tokens_per_second"] > 0, f"{name}: no generation throughput recorded"
    assert m["llama_model_load_time_seconds"] > 0, f"{name}: model load time missing"
    assert m['llama_request_latency_seconds{quantile="0.95"}'] > 0


@each_node
def test_llama_server_health_and_native_metrics(name, ip):
    status, _, body = http("GET", f"http://{ip}:{SERVER_PORT}/health")
    assert status == 200, f"{name} /health: {body[:200]!r}"

    status, _, body = http("GET", f"http://{ip}:{SERVER_PORT}/metrics")
    assert status == 200
    assert "llamacpp:" in body.decode(), f"{name}: llama-server --metrics not enabled"
