"""Tests for scripts/metrics_exporter.py: pure helpers, then the HTTP proxy
end to end against a fake llama-server (fixtures in tests/conftest.py)."""
import json
import socket
import uuid

import pytest

import metrics_exporter as me
from helpers import http, parse_metrics

# Real llama-server log lines (prefix = time since process start: min.sec.ms.us).
LOG_TWO_STARTS = """\
0.00.060.247 I srv    load_model: loading model '/opt/llama-fleet/models/qwen.gguf'
0.04.081.932 I srv  llama_server: model loaded
0.00.066.288 I srv    load_model: loading model '/opt/llama-fleet/models/qwen.gguf'
0.03.470.799 I srv  llama_server: model loaded
"""


# --- pure helpers ---------------------------------------------------------

def test_percentile_of_empty_data_is_zero():
    assert me.percentile([], 0.95) == 0.0


def test_percentile_interpolates_between_samples():
    data = [4, 1, 3, 2]  # order must not matter
    assert me.percentile(data, 0.5) == pytest.approx(2.5)
    assert me.percentile(data, 0.95) == pytest.approx(3.85)
    assert me.percentile([7], 0.95) == 7


@pytest.mark.parametrize("line, seconds", [
    ("0.04.081.932 I srv  llama_server: model loaded", 4.081932),
    ("2.03.000.500 I srv  something", 123.0005),
])
def test_log_seconds_parses_the_llama_server_prefix(line, seconds):
    assert me._log_seconds(line) == pytest.approx(seconds)


def test_log_seconds_is_none_without_a_timestamp():
    assert me._log_seconds("main: model loaded") is None


def test_model_load_time_uses_the_most_recent_start(tmp_path):
    log = tmp_path / "server.log"
    log.write_text(LOG_TWO_STARTS)
    assert me.model_load_time(str(log)) == pytest.approx(3.470799 - 0.066288)


def test_model_load_time_is_none_while_the_model_is_still_loading(tmp_path):
    log = tmp_path / "server.log"
    log.write_text(LOG_TWO_STARTS + "0.00.010.000 I srv    load_model: loading model 'x'\n")
    assert me.model_load_time(str(log)) is None


def test_model_load_time_is_none_without_a_log(tmp_path):
    assert me.model_load_time(str(tmp_path / "missing.log")) is None


def test_stats_count_errors_and_skip_missing_throughput():
    stats = me.Stats(window=3)
    stats.record(0.5, ok=True, tokens_per_second=30.0)
    stats.record(0.1, ok=False, tokens_per_second=None)
    requests, errors, latency_sum, latencies, tps = stats.snapshot()
    assert (requests, errors) == (2, 1)
    assert latency_sum == pytest.approx(0.6)
    assert tps == [30.0]  # the failed request must not drag throughput to 0


def test_stats_windows_are_bounded():
    stats = me.Stats(window=3)
    for i in range(5):
        stats.record(float(i), ok=True, tokens_per_second=float(i))
    requests, _, _, latencies, tps = stats.snapshot()
    assert requests == 5
    assert latencies == [2.0, 3.0, 4.0]
    assert tps == [2.0, 3.0, 4.0]


# --- HTTP proxy, against the fake llama-server ------------------------------

def _metrics(exporter):
    status, _, body = http("GET", f"{exporter.url}/metrics")
    assert status == 200
    return parse_metrics(body.decode())


def _access_log(exporter):
    return [json.loads(line) for line in exporter.access_log.read_text().splitlines()]


def test_proxies_completion_and_records_throughput(exporter, fake_llama):
    status, _, body = http("POST", f"{exporter.url}/completion", {"prompt": "hi", "n_predict": 8})

    assert status == 200
    assert json.loads(body) == fake_llama.response  # passed through untouched
    assert fake_llama.requests[0]["path"] == "/completion"
    assert fake_llama.requests[0]["body"] == {"prompt": "hi", "n_predict": 8}

    m = _metrics(exporter)
    assert m["llama_requests_total"] == 1
    assert m["llama_request_errors_total"] == 0
    assert m["llama_tokens_per_second"] == pytest.approx(31.5)
    assert m["llama_request_latency_seconds_count"] == 1
    assert m['llama_request_latency_seconds{quantile="0.95"}'] > 0


def test_propagates_a_caller_supplied_correlation_id(exporter, fake_llama):
    _, headers, _ = http("POST", f"{exporter.url}/completion", {"prompt": "hi"},
                         headers={"X-Correlation-Id": "req-123"})

    assert headers["X-Correlation-Id"] == "req-123"
    assert fake_llama.requests[0]["headers"]["X-Correlation-Id"] == "req-123"
    entry = _access_log(exporter)[-1]
    assert entry["event"] == "request"
    assert entry["correlation_id"] == "req-123"
    assert entry["status"] == 200
    assert entry["tokens_per_second"] == pytest.approx(31.5)


def test_mints_a_correlation_id_when_none_is_given(exporter):
    _, headers, _ = http("POST", f"{exporter.url}/completion", {"prompt": "hi"})

    uuid.UUID(headers["X-Correlation-Id"])  # raises if not a UUID
    assert _access_log(exporter)[-1]["correlation_id"] == headers["X-Correlation-Id"]


def test_upstream_error_status_is_passed_through_and_counted_once(exporter, fake_llama):
    fake_llama.status, fake_llama.response = 503, {"error": "Loading model"}

    status, _, body = http("POST", f"{exporter.url}/completion", {"prompt": "hi"})

    assert status == 503
    assert json.loads(body) == {"error": "Loading model"}
    m = _metrics(exporter)
    assert (m["llama_requests_total"], m["llama_request_errors_total"]) == (1, 1)
    assert m["llama_tokens_per_second"] == 0


def test_unreachable_upstream_is_a_502_counted_once(exporter, monkeypatch):
    with socket.socket() as s:  # grab a free port, then leave nothing listening on it
        s.bind(("127.0.0.1", 0))
        closed_port = s.getsockname()[1]
    monkeypatch.setattr(me.Config, "upstream", f"http://127.0.0.1:{closed_port}")

    status, _, body = http("POST", f"{exporter.url}/completion", {"prompt": "hi"})

    assert status == 502
    assert "error" in json.loads(body)
    m = _metrics(exporter)
    assert (m["llama_requests_total"], m["llama_request_errors_total"]) == (1, 1)
    assert "error" in _access_log(exporter)[-1]


def test_model_load_time_is_exported_only_once_known(exporter):
    assert "llama_model_load_time_seconds" not in _metrics(exporter)

    exporter.server_log.write_text(LOG_TWO_STARTS)

    assert _metrics(exporter)["llama_model_load_time_seconds"] == pytest.approx(3.4045, abs=1e-3)


def test_health_and_unknown_paths(exporter):
    assert http("GET", f"{exporter.url}/health")[::2] == (200, b"ok")
    assert http("GET", f"{exporter.url}/nope")[0] == 404
