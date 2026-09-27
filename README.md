# devops-local-fleet-cicd

Locally testable, production-shaped CI/CD pipeline that provisions a small VM fleet with
Terraform, configures it with Ansible, builds `llama.cpp` from source, deploys a GGUF model,
and performs health-checked rolling updates — all driven by GitHub Actions workflows run
locally via [`nektos/act`](https://github.com/nektos/act).

## Architecture

```
 Developer
   │
   ├─ push / PR ───────────▶ ci-lint.yml   lint, pytest, terraform test, molecule
   │                         plan.yml      terraform plan (never applies)
   │
   ├─ make deploy ─────────▶ scripts/deploy.sh ──┐  provision → configure → build →
   └─ manual dispatch ─────▶ rollout.yml ────────┤  rolling deploy → verify
      (self-hosted runner on the fleet host)     │
                                                 │  runs Terraform, then Ansible
                                                 ▼
 ┌────────────────┐   hosts.ini   ┌─────────────────────┐   SSH   ┌────────────────────────┐
 │   Terraform    │ ────────────▶ │       Ansible       │ ──────▶ │ Fleet: llama-node-1..3 │
 │  VM lifecycle  │ (real VM IPs) │ site, build, deploy │         │ (Multipass VMs)        │
 │  (Multipass)   │               │ and rollback plays  │         │                        │
 └────────────────┘               └─────────────────────┘         │ exporter :9100         │
                                                                  │  proxy, /metrics,      │
                                                                  │  JSON access log       │
                                                                  │     │                  │
                                                                  │     ▼                  │
                                                                  │ llama-server :8080     │
                                                                  └───────────┬────────────┘
                                                                              │ scrape :9100
                                                                              │ and :8080
                                                                              ▼
                                                   ┌─────────────────────────────────────┐
                                                   │ Prometheus → Grafana (Docker, host) │
                                                   └─────────────────────────────────────┘
```

Workflow diagrams (CI/CD, `make deploy`, rolling deploy and rollback, and the request,
metrics and log path) are in [docs/architecture.md](docs/architecture.md#workflows).

**Layers**

| Layer          | Tooling                                   | Responsibility                                  |
|----------------|--------------------------------------------|--------------------------------------------------|
| Infrastructure | Terraform (Multipass provider via `local-exec`, swappable for Lima/Vagrant) | VM lifecycle: create/destroy 2–3 Ubuntu nodes |
| Configuration  | Ansible                                   | Base packages, directories and log rotation; llama.cpp build, model staging, metrics exporter |
| CI/CD          | GitHub Actions + `act`                    | Lint + molecule and plan on push/PR; rollout on manual dispatch (needs a self-hosted runner on GitHub); all runnable locally with `act` |
| Application    | llama.cpp (CPU build) + GGUF model        | Inference workload (Qwen2.5-0.5B-Instruct-GGUF)  |
| Deployment     | `ansible/playbooks/deploy.yml` (`serial: 1`) | One-node-at-a-time rollout with health gating and automatic rollback |
| Observability  | `/metrics` (Prometheus format) + Grafana  | tokens/sec, p50/p95 latency, load time, req/err counts |

## Prerequisites

- macOS or Linux host (Windows via WSL2 is untested, see below)
- [Multipass](https://multipass.run/) (VM backend) — or swap the Terraform module for Lima/Vagrant, see `terraform/modules/vm/main.tf`
- [Terraform](https://developer.hashicorp.com/terraform) >= 1.6 or OpenTofu
- [Ansible](https://docs.ansible.com/) >= 2.15
- [Docker](https://www.docker.com/products/docker-desktop/) (required by `act`)
- [nektos/act](https://github.com/nektos/act)
- Python 3.12+ (`make setup` installs the pinned tools from `scripts/requirements.txt` into `.venv`, which every `make` target uses)
- `shellcheck`, `actionlint`, `tflint` on PATH for `make lint` (`ansible-lint` and `yamllint` come with `make setup`)

### Setup by operating system

**macOS** (tested: Apple M1, 8 GB, macOS 26)

```bash
brew install --cask multipass docker
brew install hashicorp/tap/terraform act tflint shellcheck actionlint jq python@3.13
make setup
```

Give your terminal or editor **Local Network** access (System Settings → Privacy &
Security → Local Network). Without it macOS blocks it from reaching the VMs, and Ansible and
`make verify` fail with "No route to host" even though `multipass list` shows them running.

**Linux** (Ubuntu/Debian; not tested here)

```bash
sudo snap install multipass
sudo apt-get install -y python3 python3-venv jq shellcheck   # Python must be 3.12+ (Ubuntu 24.04 ships it)
# Terraform:     https://developer.hashicorp.com/terraform/install (HashiCorp apt repo)
# Docker Engine: https://docs.docker.com/engine/install/  then  sudo usermod -aG docker "$USER"
# act, tflint, actionlint: release binaries from their GitHub pages
make setup
```

**Windows** (untested). The scripts are bash and the Makefile expects a Unix shell, so the
likely route is WSL2 (Ubuntu) for the tooling, with Multipass installed on Windows:

1. Install Multipass for Windows (Hyper-V) and Docker Desktop with the WSL2 backend.
2. In WSL2, install the Linux tools above and point `multipass` at the Windows binary:
   `sudo ln -s "/mnt/c/Program Files/Multipass/bin/multipass.exe" /usr/local/bin/multipass`.
3. So WSL2 can reach the VMs' IPs, set `networkingMode=mirrored` under `[wsl2]` in
   `%UserProfile%\.wslconfig` (Windows 11 22H2+), then run `wsl --shutdown`.
4. `make setup`.

Known gap: `terraform/modules/vm/scripts/create.sh` passes Multipass a cloud-init file
under the WSL `/tmp`, which `multipass.exe` may not be able to read.

## Quickstart

```bash
make setup        # install/check local tooling, python venv, ansible collections
make act-plan      # runs .github/workflows/plan.yml locally via act — terraform plan + lint
make act-rollout    # runs rollout.yml via act: static checks, plus e2e against the live fleet if it's up
make deploy          # runs the REAL pipeline on this host: provision, build, rolling deploy, verify, e2e tests
make verify         # inference smoke test + health check across the fleet
make observability  # Prometheus + Grafana on localhost, scraping the live fleet
make test           # fast tests, no VMs/Docker: terraform plan assertions + pytest
make test-e2e       # functional tests against the live fleet (after make deploy)
make test-rollback  # ships a broken release to one node and checks it's rolled back
make test-all       # test + molecule + test-e2e + test-rollback
make molecule       # converge + idempotence + verify the Ansible roles in Docker
```

> **Why both `act-rollout` and `deploy`?** `act` runs every job inside a Docker container,
> which has no route to the host's Multipass/hypervisor — so the fleet-provisioning steps
> in `rollout.yml` cannot create real VMs when run through `act`, regardless of which of
> Multipass/Lima/Vagrant you pick, **and regardless of host OS** (macOS, Linux, Windows all
> hit this identically — it's Docker container isolation, not a platform quirk). `act-rollout`
> is for validating the workflow's logic and job orchestration locally; `deploy` runs those
> same steps directly on the host, where Multipass actually works. See
> `docs/troubleshooting.md` for details.

Tear down with:

```bash
make destroy
```

## Repository layout

```
terraform/          VM lifecycle (Multipass/Lima/Vagrant-backed)
ansible/             Configuration management, build, deploy, molecule tests
.github/workflows/   act-compatible GitHub Actions (lint, plan, rollout)
scripts/             setup, act wrappers, health checks, rolling update, rollback
observability/       Prometheus + Grafana config
docs/                Architecture + troubleshooting
tests/               End-to-end smoke test
```

## Local-first CI with act

`ci-lint.yml` (lint, tests, molecule) and `plan.yml` run unmodified on GitHub-hosted runners
and locally via `act`. `rollout.yml` drives Multipass and SSHes into the VMs, so on GitHub it
only makes sense on a self-hosted runner on the machine that hosts the fleet: it's triggered
manually (`workflow_dispatch`), runs on the runner label in the `FLEET_RUNNER` repository
variable, and its `provision` job stops with an explanation if the runner can't reach
Multipass. Under act the same workflow runs static checks, and the e2e tests when a fleet
is up.

```bash
act -j lint          # static analysis only
act -j plan -s GITHUB_TOKEN=$(gh auth token)   # terraform plan (no apply)
act workflow_dispatch -W .github/workflows/rollout.yml   # all 4 rollout jobs, static checks under act
act -W .github/workflows/ci-lint.yml -j molecule          # molecule in a container (uses the host's Docker)
```

`act` runs against `catthehacker/ubuntu:act-latest` images; secrets are supplied via
`.secrets` (gitignored, see `.env.example`) rather than hardcoded. Artifact upload steps
are skipped automatically under `act` (`if: ${{ !env.ACT }}`), since `act` doesn't emulate
GitHub's real Actions Artifacts API.

## Rolling updates & rollback

`ansible/playbooks/deploy.yml` updates one node at a time (`serial: 1`). For each node it
points the `current` symlink at the new release, restarts `llama-server` and the metrics
exporter, and sends an inference request through the exporter as a health check, which
must return generated text, not just a 200. Only if that passes does it move on to the next
node. If it fails, the node is switched back to the release it was serving before (recorded
in `.previous`), the restored release must pass the same inference check, and the rollout
stops, leaving the remaining nodes on the old version. `make test-rollback` exercises exactly this with a
deliberately broken release.

Each release in `/opt/llama-fleet/releases/<timestamp>/` is a statically linked
`llama-server`, so rolling back restores the old code, not just an old launcher.

There's no load balancer in front of the fleet: clients call a node directly. While a node
restarts (a few seconds to load the model) that node is unavailable and the others keep
serving. `scripts/rolling_update.sh` runs the same per-node flow from the shell, and
`scripts/rollback.sh <node>` rolls a single node back by hand.

## Observability

Each node runs a small sidecar (`scripts/metrics_exporter.py`, port 9100) that proxies
completion requests to `llama-server`. The deploy health gate, `make verify` and
`scripts/health_check.sh` all send their inference checks through it, so its metrics
reflect real traffic:

| Metric (`:9100/metrics`)               | Source |
|----------------------------------------|--------|
| `llama_tokens_per_second`              | llama-server's own `timings.predicted_per_second`, averaged over recent requests |
| `llama_request_latency_seconds` p50/p95 | measured per proxied request (plus `_sum`/`_count`) |
| `llama_model_load_time_seconds`        | parsed from llama-server's log ("loading model" → "model loaded") for its latest start |
| `llama_requests_total`, `llama_request_errors_total` | counted per proxied request |

llama-server's native `--metrics` endpoint (`:8080/metrics`, `llamacpp:*` series) is
scraped as well.

### Prometheus and Grafana

`make observability` renders Prometheus scrape targets from the live Ansible inventory
(`scripts/gen_prom_targets.sh`) and starts Prometheus (<http://localhost:9090>) and Grafana
(<http://localhost:3000>, anonymous read-only; admin password from `GRAFANA_ADMIN_PASSWORD`,
default `admin`) via `observability/docker-compose.yml`, starting Docker Desktop first on
macOS if it isn't running. `make observability-down` stops them.

Prometheus scrapes every 5 s with two jobs, one target per node each. The targets live in
`observability/prometheus/targets/*.json` and are picked up without a restart, so re-run
`make observability` (or just `scripts/gen_prom_targets.sh`) after the fleet changes.

| Job | Endpoint | Series |
|---|---|---|
| `llama-exporter` | `<node>:9100/metrics` | the `llama_*` metrics above |
| `llama-server` | `<node>:8080/metrics` | llama-server's `llamacpp:*` metrics |

Grafana is provisioned with the Prometheus datasource and the **llama.cpp Fleet
Inference** dashboard (<http://localhost:3000/d/llama-fleet/llama-cpp-fleet-inference>),
which opens by default. Its panels: nodes up, model load time, requests, request errors,
tokens/sec, latency p50/p95, request rate and error rate.

Checked on the 2-node fleet on 2026-09-27: all four targets `up`, and Prometheus returning
`llama_tokens_per_second` 32.6 / 34.7, `llama_model_load_time_seconds` 2.01 / 2.21
(llama-node-1 / llama-node-2) and 30 `llamacpp:*` series across the two nodes; the same
tokens/sec query run through Grafana's datasource returned both nodes. To check it yourself:

```bash
curl -s localhost:9090/api/v1/targets | jq -r '.data.activeTargets[] | "\(.labels.job) \(.labels.node) \(.health)"'
curl -s 'localhost:9090/api/v1/query?query=llama_tokens_per_second' | jq '.data.result'
```

### Logging and correlation IDs

All logs live on each node under `/var/log/llama-fleet/`:

| File | Format | Written by | Rotation |
|---|---|---|---|
| `metrics-exporter.jsonl` | JSON, one line per request | the sidecar | daily, 7 kept, compressed |
| `llama-server.err.log` | plain text | llama-server (stderr) | none |
| `llama-server.jsonl` | — (stays empty) | llama-server (stdout) | daily, 7 kept, compressed |

Every request through the sidecar gets a correlation ID. If the client sends
`X-Correlation-Id`, that value is kept; otherwise the sidecar generates a UUID. The ID is
returned in the response's `X-Correlation-Id` header, forwarded to llama-server, and
written to `metrics-exporter.jsonl` with the path, status, latency and tokens/sec, plus
an `error` field when llama-server can't be reached:

```json
{"event": "request", "correlation_id": "health-check-1790517410-69985", "path": "/completion", "status": 200, "latency_s": 0.2685, "tokens_per_second": 32.09978447287568, "ts": 1790517411.0421784}
```

`scripts/health_check.sh` sends IDs of the form `health-check-<epoch>-<pid>` and the e2e
tests send `e2e-<uuid>`, so their requests are easy to pick out; the deploy health gate
sends none and gets a generated UUID. To find a request by its ID:

```bash
ansible llama_fleet -m ansible.builtin.shell -a 'grep "<correlation-id>" /var/log/llama-fleet/metrics-exporter.jsonl'
```

If the log directory isn't writable, the sidecar prints the same JSON lines to stdout, where
journald picks them up (`journalctl -u metrics-exporter`).

Known gaps:

- **llama-server doesn't log in JSON.** The systemd unit sets `LLAMA_LOG_JSON=1`, but the
  pinned build still writes plain text to `llama-server.err.log`, and `llama-server.jsonl`
  stays empty.
- **llama-server doesn't log the correlation ID.** The sidecar forwards it, but llama-server
  ignores it, so matching a sidecar log line to llama-server's lines for the same request
  (`slot … | task N | …`) is by timestamp only.
- **`llama-server.err.log` isn't rotated.** logrotate only covers `*.jsonl`.
- **Logs stay on the nodes.** Nothing ships them to a central store; Grafana shows metrics
  only.

### Observed metrics

Measured on the 2-node fleet from `make deploy`: an Apple M1 host (8 cores, 8 GB RAM,
macOS 26, Multipass 1.16), each node 2 vCPU / 2 GB, running the llama.cpp v0.5.0 CPU build
with Qwen2.5-0.5B-Instruct Q4_K_M. Workload: exporter counters reset, then 20 sequential
completion requests per node (same prompt, 64 tokens each), with both nodes busy at once.

| Metric | llama-node-1 | llama-node-2 |
|---|---|---|
| Generation throughput (`llama_tokens_per_second`) | 16.4 tok/s | 18.9 tok/s |
| Latency p50 (`llama_request_latency_seconds`) | 4.63 s | 3.36 s |
| Latency p95 | 5.08 s | 3.68 s |
| Model load time (`llama_model_load_time_seconds`) | 3.84 s | 4.15 s |
| Requests / errors | 20 / 0 | 20 / 0 |

Short requests on an otherwise idle fleet run faster: the 8-token check in `make verify`
reports about 33–36 tok/s per node. Both VMs share the host's cores, so throughput drops
when they generate at the same time. Model load time falls to about 1.7–1.8 s when the GGUF
is already in the node's page cache.

Example `:9100/metrics` from llama-node-1 after that workload:

```
llama_requests_total 20
llama_request_errors_total 0
llama_request_latency_seconds{quantile="0.5"} 4.6343
llama_request_latency_seconds{quantile="0.95"} 5.0808
llama_request_latency_seconds_sum 83.7837
llama_request_latency_seconds_count 20
llama_tokens_per_second 16.4487
llama_model_load_time_seconds 3.8442
llama_uptime_seconds 86
```

And one line of its access log (`/var/log/llama-fleet/metrics-exporter.jsonl`):

```json
{"event": "request", "correlation_id": "readme-bench-20", "path": "/completion", "status": 200, "latency_s": 2.7106, "tokens_per_second": 23.599058734685897, "ts": 1790513755.8513744}
```

## Testing

| Command | Needs | What it checks | In CI |
|---|---|---|---|
| `make test` | nothing | `terraform test` plan assertions, plus pytest `tests/unit/` | `lint` workflow, `lint` job |
| `make molecule` | Docker | Ansible roles converge, are idempotent, and build a working `llama-server` | `lint` workflow, `molecule` job |
| `make test-e2e` | the live fleet | pytest `tests/e2e/` against every node in the inventory (also the last step of `make deploy`) | `rollout` workflow, `verify` job |
| `make test-all` | Docker + fleet | all of the above | — |
| `make verify` | the live fleet | the shell smoke test `make deploy` runs after the rollout | `rollout` workflow, `verify` job |

The `verify` job gets the inventory from the `provision` job's artifact on GitHub, and
fails rather than skipping if it's missing. Under act there are no artifacts, so
`make act-rollout` passes in the local inventory when the fleet from `make deploy` is
answering; the job then runs `verify.sh` and the e2e tests against it from inside the act
container, which can reach the VMs over HTTP. With no live fleet it falls back to static
checks.

- **Terraform** (`terraform/tests/plan.tftest.hcl`): node naming and count, the 2–3 node and
  backend validations, the inventory path, and that VM sizing reaches the VM module's
  create script. Plan-only, so no VMs are created.
- **Metrics exporter** (`tests/unit/test_metrics_exporter.py`): percentile, log-timestamp and
  model-load-time parsing, and the proxy end to end against a fake llama-server:
  pass-through of bodies and status codes, tokens/sec, correlation-ID propagation and JSON
  access logs, errors counted exactly once, 502 when llama-server is down.
- **Scripts** (`tests/unit/test_scripts.py`): `health_check.sh` pass/fail cases,
  `gen_prom_targets.sh` rendering targets from an inventory, and `verify.sh` failing (not
  passing) when the inventory has no hosts.
- **Molecule** (`ansible/molecule/llama_cpp`): converges `common` + `llama_cpp`, runs them a
  second time to check idempotence, then verifies the pinned release was built and runs.
- **E2E** (`tests/e2e/test_fleet.py`): each node generates text through the exporter
  proxy with its correlation ID echoed, reports non-zero tokens/sec, latency and model load
  time, and serves llama-server's own `/health` and `/metrics`. Skipped if there's no inventory.

## Reproducibility

Everything a build depends on is pinned:

- **llama.cpp**: release tag `llama_cpp_ref` in `ansible/inventory/group_vars/all.yml`.
- **Model**: Hugging Face commit `model_revision` plus `model_sha256`, verified on every run.
- **Terraform**: CLI version in the workflows, provider constraints in `providers.tf`, and
  exact provider hashes (macOS + Linux) in the committed `.terraform.lock.hcl`.
- **Python tooling** (Ansible, ansible-lint, yamllint, molecule): exact versions in
  `scripts/requirements.txt`; Galaxy collections in `ansible/requirements.yml`.
- **Containers**: Prometheus/Grafana image tags, and the molecule image by digest.

## Security

- No credentials are committed; `.gitignore` excludes `*.tfstate*`, `.secrets`, `*.pem`,
  `ansible/inventory/hosts.ini` (generated from the `.tpl`), and `.env`.
- No node-side secrets are needed today. If you add one, put it in
  `ansible/inventory/group_vars/llama_fleet/vault.yml` (gitignored) encrypted with
  `ansible-vault`; see `vault.yml.example` next to it.
- Terraform state is local-only (`terraform/providers.tf`), with a documented remote-backend
  upgrade path in `docs/architecture.md` for anyone productionizing this.

## Resource requirements (observed)

| Step                         | Time (M2 Pro, 16GB, 3 nodes) | Notes                          |
|------------------------------|-------------------------------|---------------------------------|
| `make setup`                  | ~3 min                        | one-time                        |
| `terraform apply`             | ~90s                           | 3× 2 vCPU / 2GB Multipass VMs   |
| llama.cpp build (per node)    | ~2–4 min                       | CPU-only, `-O3`, no GPU         |
| Model pull (Qwen2.5-0.5B Q4)  | ~30s                            | ~350MB GGUF                     |
| Rolling update (3 nodes)      | ~3 min                          | sequential, health-gated        |

CPU-only by design 

See `docs/architecture.md` for component-level detail and diagrams, and
`docs/troubleshooting.md` for common local setup issues.
