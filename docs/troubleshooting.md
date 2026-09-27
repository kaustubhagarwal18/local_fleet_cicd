# Troubleshooting

**Ansible fails with `ubuntu@<ip>: Permission denied (publickey)`**
Multipass does not expose a key usable by plain `ssh`/Ansible by default — it manages its own
internal keypair for `multipass shell`/`exec` only. `scripts/ensure_ssh_key.sh` generates a
project-local keypair, and `terraform/modules/vm/scripts/create.sh` injects the public half
into each VM via cloud-init at launch; Ansible is pointed at the private half through
`ansible_ssh_private_key_file` in the generated inventory. **This only takes effect on newly
created VMs** — cloud-init runs once, at first boot, so any VMs created before this fix (or
before `scripts/ensure_ssh_key.sh` existed) will keep failing this way forever. Fix:
```
make destroy
make deploy
```
`make deploy` regenerates the key (if missing) and recreates the fleet with it correctly
injected. This is a one-time fix — normal re-runs of `make deploy` afterward reuse the
existing key and VMs without issue.

**`ansible-playbook` fails immediately with `The 'community.general.yaml' callback plugin has been removed`**
`community.general` 12.0.0+ dropped that callback plugin; it's superseded by `result_format=yaml`
on the built-in `default` callback (ansible-core 2.13+). This repo's `ansible.cfg` already uses
the current form (`stdout_callback = default` + a `[callback_default] result_format = yaml`
section) — if you still hit this, your `ansible.cfg` predates that fix or was hand-edited back;
diff it against the version in this repo.

**Why `make act-rollout` doesn't create VMs**
`act` runs every job inside a Docker container, which has no route to the *host's*
Multipass binary or hypervisor — on macOS, Linux, or Windows alike, and installing Multipass
in the container doesn't help (there is no hypervisor to nest into). So under act
(`env.ACT` set) `rollout.yml` swaps each fleet-touching step for a static check:
`terraform validate` + `plan` in `provision`, `ansible-playbook --syntax-check` in `build`
and `rollout`, and `bash -n` on the verify scripts. `make act-rollout` passing means the
pipeline's *shape* is sound — not that a fleet exists. To provision and deploy a real fleet, run
`make deploy` (or `scripts/deploy.sh`), which performs the same steps directly on the host,
outside act, on any OS:
```
make deploy
```
which runs, in order: `terraform apply` → `ansible-playbook site.yml` → `build.yml` → `deploy.yml` →
`scripts/verify.sh`.

**"No route to host" to the VMs on macOS, though `multipass list` shows them running**
macOS Local Network privacy is blocking the app you're running from (seen with VS Code's
terminal): even `ping` to the VM IPs fails with "sendto: No route to host". Allow the app
in System Settings → Privacy & Security → Local Network, then restart it. The Multipass
daemon itself isn't affected, which is why `multipass list` still looks fine.

**VMs freeze, Docker Desktop crashes, or `multipass exec` times out ("Host is down")**
The host ran out of memory. Each node takes 2 GB and Docker Desktop reserves its own VM
(4 GB by default on the 8 GB Mac this was built on). Three nodes plus Docker didn't fit in
8 GB; two nodes do, as long as the heavy Docker jobs (act, molecule) run with the VMs
stopped: `multipass stop llama-node-1 llama-node-2`, run them, quit Docker, then
`make deploy` starts the VMs again. A frozen VM needs `multipass stop --force` and
`multipass start`. Use `vm_count = 2` in `terraform/terraform.tfvars` on small hosts.

**`multipass launch` hangs or fails on first run**
The Multipass driver VM (hyperkit/QEMU on macOS, LXD on Linux) needs to initialize once.
Run `multipass version` and `multipass list` first; on macOS, ensure the Multipass system
service is running (`System Settings → General → Login Items & Extensions`).

**`terraform apply` succeeds but Ansible can't reach the nodes**
Check `ansible/inventory/hosts.ini` was regenerated (Terraform writes it via `local_file` —
delete it and re-`apply` if it looks stale). Confirm `ssh ubuntu@<ip>` works manually first;
cloud-init on a fresh VM can take 15-30s after `multipass launch` returns.

**`act` can't find Docker / jobs hang, or "Cannot connect to the Docker daemon"**
`act` runs each job in a Docker container, so Docker Desktop (or another daemon) must be
running first. `make setup`, `scripts/act-plan.sh` and `scripts/act-rollout.sh` all call
`scripts/ensure_docker.sh`, which tries to auto-launch Docker Desktop on macOS and polls
until it's ready; on Linux it prints the right `systemctl start docker` instead of guessing.
If you're nesting virtualization (Multipass + act's Docker containers both want a
hypervisor) on constrained hardware, run `act` steps on the host and only use Multipass for
the fleet nodes, not for act's own runners.

**First `act` run asks about image size, or warns about Apple M-series architecture**
Both are one-time, per-machine `act` setup questions, unrelated to this repo. Pick the
"Medium" image (Micro can't install `terraform`/`ansible-lint`/etc.; Large is 75GB and
unnecessary here). On Apple Silicon, add `--container-architecture linux/amd64` to
`~/Library/Application Support/act/actrc` to silence the recurring warning.

**`act` leaves containers running after a failed job**
This is `act`'s own documented behavior — it keeps the container up after a failure (unless
`--rm` is passed) so you can `docker exec` in to debug, and only auto-removes it on success.
Clean up manually with `docker rm -f <container>` or `docker container prune -f`, or pass
`--rm` to `act` to always remove containers.

**`terraform fmt -check` or `tflint` fails**
Run `make fmt` (canonical HCL formatting) before committing. `tflint` flags genuinely unused
variables — remove them rather than suppressing the rule.

**`ansible-lint` reports "role not found" (`syntax-check[specific]`)**
`ansible.cfg` must be at the **repo root**, not inside `ansible/` — its `roles_path` and
`inventory` settings resolve relative to the config file's own location. Run `ansible-lint`
and all playbooks from the repo root.

**`ansible-lint` complains about `.yamllint.yml` being "incompatible"**
`ansible-lint`'s `production` profile requires `comments.min-spaces-from-content: 1`,
`braces.max-spaces-inside: 1`, and `octal-values.forbid-implicit-octal`/`forbid-explicit-octal: true`
in your yamllint config — see `.yamllint.yml` in this repo for a working baseline.

**`ansible-lint` flags `var-naming[no-role-prefix]` or `name[casing]`**
Every `register:` variable inside a role must be prefixed with that role's name (e.g.
`llama_cpp_health`, not `health`), and every task/handler `name:` must start with a capital
letter. Already applied throughout `ansible/roles/`; keep the convention for anything you add.

**llama.cpp build fails with a CMake/compiler error**
Confirm `build-essential` and `cmake >= 3.14` are present (the `common` role installs
these). If you're on an ARM host targeting an ARM VM image, no cross-compilation is needed;
mismatched host/VM architectures will need `-DCMAKE_SYSTEM_PROCESSOR` overrides not covered
by this scaffold.

**Health check keeps failing / rollback loop**
Check `/var/log/llama-fleet/llama-server.err.log` on the node — most often the model file
failed to download fully (see `ansible/roles/model/tasks/main.yml`; re-run `build.yml`,
it's idempotent and will re-fetch if the stat check fails) or the port is already bound by
a previous, un-stopped `llama-server` process from a manual test run.
