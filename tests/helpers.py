"""Small HTTP and Prometheus-parsing helpers shared by the unit and e2e tests."""
import json
import urllib.error
import urllib.request


def http(method, url, body=None, headers=None, timeout=10):
    """Make a request and return (status, headers, body bytes), including for 4xx/5xx."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.headers, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.headers, exc.read()


def parse_metrics(text):
    """Parse Prometheus text format into {"name{labels}": float}, skipping comments."""
    samples = {}
    for line in text.splitlines():
        if line and not line.startswith("#"):
            name, value = line.rsplit(" ", 1)
            samples[name] = float(value)
    return samples
