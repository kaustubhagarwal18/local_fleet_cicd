"""Shared pytest fixtures: a fake llama-server and an in-process metrics exporter.

Both listen on random localhost ports, so the unit/integration tests need no
VMs, Docker or network access.
"""
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "scripts"))  # metrics_exporter
sys.path.insert(0, str(REPO / "tests"))    # smoke_test

import metrics_exporter  # noqa: E402

COMPLETION = {
    "content": " all systems nominal",
    "timings": {"predicted_n": 8, "predicted_per_second": 31.5},
}


def _serve(handler):
    """Start `handler` on a random localhost port in a daemon thread."""
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    # Short poll interval so shutdown() at fixture teardown is near-instant.
    threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    return server


@pytest.fixture
def fake_llama():
    """A stand-in llama-server answering POST /completion.

    Set `.status` / `.response` to change what it returns; `.requests`
    records what it received (path, headers, JSON body).
    """
    state = SimpleNamespace(status=200, response=dict(COMPLETION), requests=[])

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_):
            """Keep test output quiet."""

        def do_POST(self):
            """Record the request and answer with the configured status/body."""
            payload = self.rfile.read(int(self.headers.get("Content-Length", 0)))
            state.requests.append({
                "path": self.path,
                "headers": dict(self.headers),
                "body": json.loads(payload or b"{}"),
            })
            body = json.dumps(state.response).encode()
            self.send_response(state.status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = _serve(Handler)
    state.port = server.server_port
    state.url = f"http://127.0.0.1:{server.server_port}"
    yield state
    server.shutdown()
    server.server_close()


@pytest.fixture
def exporter(fake_llama, tmp_path, monkeypatch):
    """The real metrics exporter, proxying to `fake_llama`, with fresh stats and temp logs."""
    access_log = tmp_path / "metrics-exporter.jsonl"
    server_log = tmp_path / "llama-server.err.log"
    monkeypatch.setattr(metrics_exporter.Config, "upstream", fake_llama.url)
    monkeypatch.setattr(metrics_exporter.Config, "access_log", str(access_log))
    monkeypatch.setattr(metrics_exporter.Config, "server_log", str(server_log))
    monkeypatch.setattr(metrics_exporter.Config, "timeout", 5.0)
    monkeypatch.setattr(metrics_exporter, "STATS", metrics_exporter.Stats(window=200))

    server = _serve(metrics_exporter.Handler)
    yield SimpleNamespace(
        url=f"http://127.0.0.1:{server.server_port}",
        access_log=access_log,
        server_log=server_log,
    )
    server.shutdown()
    server.server_close()
