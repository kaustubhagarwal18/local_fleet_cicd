#!/usr/bin/env python3
"""
Metrics + access-log sidecar for llama-server (stdlib only).

Every POST (e.g. /completion) is proxied to the local llama-server. For each
one it records latency, generation throughput (llama-server's own
`timings.predicted_per_second`), success/failure, and writes a JSON access-log
line carrying a correlation ID, which is also returned as `X-Correlation-Id`
(an incoming `X-Correlation-Id` is propagated instead of minting a new one).
The deploy health gate, scripts/health_check.sh and scripts/verify.sh all
send their inference checks through this proxy, so the metrics reflect real
traffic rather than sitting at zero.

GET /metrics (Prometheus text format):
  llama_requests_total                        counter
  llama_request_errors_total                  counter
  llama_request_latency_seconds{quantile=...}  summary: p50/p95 over the last
                                              --window requests, plus _sum/_count
  llama_tokens_per_second                     gauge: mean generation tok/s over
                                              the last --window successful requests
  llama_model_load_time_seconds               gauge: "loading model" -> "model
                                              loaded" for llama-server's latest
                                              start, parsed from its log
  llama_uptime_seconds                        gauge: exporter uptime
GET /health -> 200 "ok" (exporter liveness only)
"""
import argparse
import json
import os
import re
import threading
import time
import uuid
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib import error as urlerror
from urllib import request as urlrequest

START_TIME = time.time()

# llama-server log lines start with the time since process start, formatted
# minutes.seconds.milliseconds.microseconds, e.g. "0.04.081.932 I srv ...".
LOG_TS_RE = re.compile(r"^(\d+)\.(\d{2})\.(\d{3})\.(\d{3}) ")


class Config:
    """Runtime settings, overridden from the command line in main()."""

    upstream = "http://127.0.0.1:8080"
    access_log = "/var/log/llama-fleet/metrics-exporter.jsonl"
    server_log = "/var/log/llama-fleet/llama-server.err.log"
    timeout = 120.0


class Stats:
    """Request counters plus sliding windows for quantiles and throughput."""

    def __init__(self, window):
        """Keep quantile/throughput samples for the last `window` requests."""
        self._lock = threading.Lock()
        self.requests = 0
        self.errors = 0
        self.latency_sum = 0.0
        self.latencies = deque(maxlen=window)
        self.tokens_per_second = deque(maxlen=window)

    def record(self, latency, ok, tokens_per_second):
        """Record one proxied request.

        `ok` is False for non-2xx responses and unreachable upstreams.
        `tokens_per_second` is None when the response carried no timings
        (e.g. errors), so it doesn't drag the throughput average to zero.
        """
        with self._lock:
            self.requests += 1
            self.latency_sum += latency
            self.latencies.append(latency)
            if not ok:
                self.errors += 1
            if tokens_per_second is not None:
                self.tokens_per_second.append(tokens_per_second)

    def snapshot(self):
        """Return a consistent copy for rendering /metrics.

        Returns (requests, errors, latency_sum, latencies, tokens_per_second);
        the two lists hold the recent-window samples.
        """
        with self._lock:
            return (self.requests, self.errors, self.latency_sum,
                    list(self.latencies), list(self.tokens_per_second))


STATS = Stats(window=200)


def log_json(record):
    """Append `record` as one JSON line to the access log, adding a `ts` if missing."""
    record.setdefault("ts", time.time())
    line = json.dumps(record)
    try:
        with open(Config.access_log, "a") as f:
            f.write(line + "\n")
    except OSError:
        print(line, flush=True)  # log dir not writable (e.g. local dev): journald still gets JSON


def percentile(data, pct):
    """Linear-interpolated percentile of `data` for `pct` in [0, 1]; 0.0 if empty."""
    if not data:
        return 0.0
    data = sorted(data)
    k = (len(data) - 1) * pct
    f, c = int(k), min(int(k) + 1, len(data) - 1)
    return data[f] if f == c else data[f] + (data[c] - data[f]) * (k - f)


def _log_seconds(line):
    """Seconds since llama-server start from a log line's prefix, or None if it has none."""
    m = LOG_TS_RE.match(line)
    if not m:
        return None
    minutes, seconds, millis, micros = (int(g) for g in m.groups())
    return minutes * 60 + seconds + millis / 1e3 + micros / 1e6


def model_load_time(path, tail_bytes=512 * 1024):
    """Seconds llama-server spent loading the model on its most recent start.

    The log is appended across restarts, so take the last "loading model"
    line and the first "model loaded" line after it. Returns None until the
    model has finished loading (or if the log isn't readable).
    """
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode(errors="replace").splitlines()
    except OSError:
        return None
    started, result = None, None
    for line in lines:
        if "load_model: loading model" in line:
            started, result = _log_seconds(line), None
        elif "model loaded" in line and started is not None:
            loaded = _log_seconds(line)
            if loaded is not None:
                result = loaded - started
            started = None
    return result


class Handler(BaseHTTPRequestHandler):
    """Serves /metrics and /health, and proxies every POST to llama-server."""

    def log_message(self, *_):
        """Silence the default stderr access log; do_POST writes a JSON one instead."""

    def do_GET(self):
        """Serve /metrics and /health; anything else is a 404."""
        if self.path == "/metrics":
            self._metrics()
        elif self.path == "/health":
            self._reply(200, b"ok", "text/plain")
        else:
            self._reply(404, b"not found", "text/plain")

    def _metrics(self):
        """Render the current stats in Prometheus text exposition format."""
        requests, errors, latency_sum, latencies, tps = STATS.snapshot()
        lines = [
            "# HELP llama_requests_total Completion requests proxied to llama-server",
            "# TYPE llama_requests_total counter",
            f"llama_requests_total {requests}",
            "# HELP llama_request_errors_total Proxied requests that failed (non-2xx or upstream unreachable)",
            "# TYPE llama_request_errors_total counter",
            f"llama_request_errors_total {errors}",
            "# HELP llama_request_latency_seconds End-to-end latency of proxied requests",
            "# TYPE llama_request_latency_seconds summary",
            f'llama_request_latency_seconds{{quantile="0.5"}} {percentile(latencies, 0.5):.4f}',
            f'llama_request_latency_seconds{{quantile="0.95"}} {percentile(latencies, 0.95):.4f}',
            f"llama_request_latency_seconds_sum {latency_sum:.4f}",
            f"llama_request_latency_seconds_count {requests}",
            "# HELP llama_tokens_per_second Mean generation throughput over recent successful requests",
            "# TYPE llama_tokens_per_second gauge",
            f"llama_tokens_per_second {sum(tps) / len(tps) if tps else 0.0:.4f}",
            "# HELP llama_model_load_time_seconds Model load time on llama-server's most recent start",
            "# TYPE llama_model_load_time_seconds gauge",
        ]
        load_time = model_load_time(Config.server_log)
        if load_time is not None:  # omitted, not faked, until the model has loaded
            lines.append(f"llama_model_load_time_seconds {load_time:.4f}")
        lines += [
            "# HELP llama_uptime_seconds Exporter uptime",
            "# TYPE llama_uptime_seconds gauge",
            f"llama_uptime_seconds {time.time() - START_TIME:.0f}",
            "",
        ]
        self._reply(200, "\n".join(lines).encode(), "text/plain; version=0.0.4")

    def do_POST(self):
        """Proxy the request to llama-server, record its stats and log it.

        The upstream status and body are returned unchanged. If llama-server
        can't be reached the client gets a 502 with a JSON error. Either way
        the response carries the request's X-Correlation-Id.
        """
        correlation_id = self.headers.get("X-Correlation-Id") or str(uuid.uuid4())
        payload = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        start = time.monotonic()
        error = None
        try:
            req = urlrequest.Request(
                f"{Config.upstream}{self.path}", data=payload, method="POST",
                headers={"Content-Type": "application/json", "X-Correlation-Id": correlation_id},
            )
            with urlrequest.urlopen(req, timeout=Config.timeout) as resp:
                status, data = resp.status, resp.read()
        except urlerror.HTTPError as exc:  # upstream answered, with a 4xx/5xx
            status, data = exc.code, exc.read()
        except (urlerror.URLError, OSError) as exc:  # upstream unreachable or timed out
            status, error = 502, str(exc)
            data = json.dumps({"error": error}).encode()
        latency = time.monotonic() - start

        ok = 200 <= status < 300
        tokens_per_second = None
        if ok:
            try:
                tokens_per_second = json.loads(data).get("timings", {}).get("predicted_per_second")
            except (ValueError, AttributeError):
                pass
        STATS.record(latency, ok, tokens_per_second)

        record = {
            "event": "request",
            "correlation_id": correlation_id,
            "path": self.path,
            "status": status,
            "latency_s": round(latency, 4),
            "tokens_per_second": tokens_per_second,
        }
        if error:
            record["error"] = error
        log_json(record)
        self._reply(status, data, "application/json", {"X-Correlation-Id": correlation_id})

    def _reply(self, status, body, content_type, headers=None):
        """Send a complete response with the given status, body and extra headers."""
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)


def main():
    """Parse the command line, apply it to Config and serve until killed."""
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--port", type=int, default=9100)
    parser.add_argument("--upstream", default=Config.upstream)
    parser.add_argument("--access-log", default=Config.access_log)
    parser.add_argument("--server-log", default=Config.server_log)
    parser.add_argument("--timeout", type=float, default=Config.timeout)
    args = parser.parse_args()

    Config.upstream = args.upstream
    Config.access_log = args.access_log
    Config.server_log = args.server_log
    Config.timeout = args.timeout

    server = ThreadingHTTPServer(("0.0.0.0", args.port), Handler)
    log_json({"event": "exporter_started", "port": args.port, "upstream": args.upstream})
    server.serve_forever()


if __name__ == "__main__":
    main()
