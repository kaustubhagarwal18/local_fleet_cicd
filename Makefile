.PHONY: setup act-plan act-rollout deploy verify destroy lint fmt test test-e2e test-rollback test-all molecule observability observability-down

# Prefer the pinned tools `make setup` installs into .venv (scripts/requirements.txt)
# over whatever ansible/molecule happens to be on the host PATH. Scripts run from
# recipes inherit this PATH, but make (3.81, as shipped with macOS) looks up a
# recipe's own first word in its startup PATH, so those call $(call tool,...),
# which picks the .venv copy when there is one.
VENV_BIN := $(CURDIR)/.venv/bin
export PATH := $(VENV_BIN):$(PATH)
tool = $(if $(wildcard $(VENV_BIN)/$(1)),$(VENV_BIN)/$(1),$(1))

setup:
	@echo "==> Checking local tooling"
	@command -v terraform >/dev/null || command -v tofu >/dev/null || bash scripts/hint_terraform.sh
	@command -v ansible >/dev/null || { echo "ansible not found — pip install ansible (see README.md#prerequisites)"; exit 1; }
	@command -v multipass >/dev/null || bash scripts/hint_multipass.sh
	@command -v act >/dev/null || echo "WARN: act not found — install https://github.com/nektos/act"
	@bash scripts/ensure_docker.sh
	@bash scripts/ensure_ssh_key.sh
	python3 -m venv .venv
	. .venv/bin/activate && pip install -q -r scripts/requirements.txt
	ansible-galaxy collection install -r ansible/requirements.yml
	@echo "==> setup complete"

act-plan:
	bash scripts/act-plan.sh

act-rollout:
	bash scripts/act-rollout.sh

# Runs the real pipeline directly on this host (not through act — act's
# Docker containers have no route to the host's Multipass/hypervisor).
# See scripts/deploy.sh and docs/troubleshooting.md.
deploy:
	bash scripts/deploy.sh

verify:
	bash scripts/verify.sh

destroy:
	@if command -v terraform >/dev/null; then TF_BIN=terraform; \
	elif command -v tofu >/dev/null; then TF_BIN=tofu; \
	else echo "terraform/tofu not found"; exit 1; fi; \
	cd terraform && $$TF_BIN destroy -auto-approve

lint:
	actionlint
	$(call tool,yamllint) .
	$(call tool,ansible-lint)
	shellcheck scripts/*.sh
	cd terraform && terraform fmt -check -recursive && terraform validate

fmt:
	cd terraform && terraform fmt -recursive

# Fast, self-contained checks — no VMs, Docker or network: Terraform plan
# assertions (terraform/tests/) and pytest unit/integration tests for the
# metrics exporter and the pipeline scripts (tests/unit/). Also run in CI.
test:
	cd terraform && terraform init -input=false >/dev/null && terraform test
	$(call tool,pytest)

# Functional checks against the live fleet (run `make deploy` first):
# inference through the proxy, real metrics, llama-server health/metrics.
test-e2e:
	$(call tool,pytest) -m e2e

# Deploys a deliberately broken release to one node and checks the health
# gate rolls it back automatically (changes the live fleet for ~1 minute).
test-rollback:
	$(call tool,pytest) -m rollback -v

# Everything: fast tests, the molecule role test (needs Docker), e2e, rollback.
test-all: test molecule test-e2e test-rollback

# Converges the common + llama_cpp roles in a Docker container, checks
# idempotence, then verifies the built llama-server. Needs Docker; takes a
# few minutes (it compiles llama-server).
# DOCKER_HOST: molecule's Python Docker SDK only tries /var/run/docker.sock,
# but Docker Desktop (macOS) serves the socket named by the active docker context.
molecule:
	@bash scripts/ensure_docker.sh
	cd ansible && DOCKER_HOST="$$(docker context inspect --format '{{.Endpoints.docker.Host}}')" \
		molecule test -s llama_cpp

# Prometheus + Grafana on localhost, scraping every node in the live inventory.
observability:
	bash scripts/gen_prom_targets.sh
	@bash scripts/ensure_docker.sh
	docker compose -f observability/docker-compose.yml up -d
	@echo "==> Prometheus: http://localhost:9090/targets   Grafana: http://localhost:3000 (dashboard: llama.cpp Fleet Inference)"

observability-down:
	docker compose -f observability/docker-compose.yml down
