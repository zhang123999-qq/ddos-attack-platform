.PHONY: all build docker-build docker-push binary binary-package certs deploy-controller deploy-attacker deploy-attacker-raw clean help test test-controller test-attacker test-coverage lint

# =============================================================================
# DDoS Attack Platform — 一键构建/部署 Makefile
# =============================================================================

VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
REGISTRY ?= ghcr.io/your-org
CONTROLLER_IP ?= 10.100.1.10
DAYS_VALID_CA ?= 730
DAYS_VALID_NODE ?= 365
SHARED_SECRET ?= $(shell openssl rand -hex 32 2>/dev/null || echo "changeme32charslongsecret")

all: certs docker-build

help:
	@echo "DDoS Attack Platform v$(VERSION)"
	@echo ""
	@echo "  Quick Start (Docker):"
	@echo "    make certs               Generate mTLS certificates"
	@echo "    make docker-build        Build all Docker images locally"
	@echo "    make deploy-all          Deploy full stack (single machine)"
	@echo ""
	@echo "  Unified Deploy (Docker + Binary mixed):"
	@echo "    make configs             Generate per-node .env from config.yaml"
	@echo "    make distribute          Distribute certs+configs to all nodes"
	@echo "    make unified-deploy      Full cluster from config.yaml"
	@echo "    make unified-status      Show cluster health"
	@echo "    make unified-stop        Stop entire cluster"
	@echo ""
	@echo "  CI / Release:"

# ========== 证书 ==========
certs:
	@echo "=== Generating mTLS certificates ==="
	cd deploy && \
	CONTROLLER_IP=$(CONTROLLER_IP) \
	DAYS_VALID_CA=$(DAYS_VALID_CA) \
	DAYS_VALID_NODE=$(DAYS_VALID_NODE) \
	NODE_IPS="10.100.1.20 10.100.1.21" \
	NODE_HOSTNAMES="attacker-http-01 attacker-raw-01" \
	./generate_certs.sh
	@echo "=== Copying certs ==="
	mkdir -p controller/certs attacker/certs
	cp deploy/certs/ca-cert.pem deploy/certs/controller-cert.pem deploy/certs/controller-key.pem controller/certs/
	cp deploy/certs/nodes/attacker-http-01/* attacker/certs/
	@echo "Done! Edit config.env files now."

# ========== Docker 构建 ==========
docker-build:
	@echo "=== Building Controller ==="
	docker build -t $(REGISTRY)/ddos-attack-platform/controller:$(VERSION) -t ddos-controller:latest ./controller
	@echo "=== Building Attacker HTTP ==="
	docker build -t $(REGISTRY)/ddos-attack-platform/attacker-http:$(VERSION) -t ddos-attacker:latest ./attacker

docker-push:
	@echo "=== Pushing to $(REGISTRY) ==="
	docker push $(REGISTRY)/ddos-attack-platform/controller:$(VERSION)
	docker push $(REGISTRY)/ddos-attack-platform/attacker-http:$(VERSION)

# ========== 二进制构建 ==========
binary:
	cd build && pip install -q -r requirements-build.txt && python build.py all

binary-package:
	cd build && pip install -q -r requirements-build.txt && python build.py all && python build.py package

# ========== 统一部署 (Docker + Binary 混合) ==========
configs:
	@bash deploy/generate-configs.sh

distribute:
	@bash deploy/distribute-certs.sh

unified-deploy:
	@bash deploy/unified-deploy.sh deploy-all

unified-status:
	@bash deploy/unified-deploy.sh status

unified-stop:
	@bash deploy/unified-deploy.sh stop

# ========== 部署 (Docker) ==========
deploy-controller:
	@echo "=== Deploying Controller ==="
	SHARED_SECRET=$(SHARED_SECRET) CONTROLLER_IP=$(CONTROLLER_IP) \
	docker compose -f deploy/docker-compose.controller.yml up -d

deploy-attacker:
	@echo "=== Deploying HTTP Attacker ==="
	SHARED_SECRET=$(SHARED_SECRET) CONTROLLER_URL=https://$(CONTROLLER_IP):8443 \
	docker compose -f deploy/docker-compose.attacker.yml up -d

deploy-attacker-raw:
	@echo "=== Deploying RAW Attacker ==="
	SHARED_SECRET=$(SHARED_SECRET) CONTROLLER_URL=https://$(CONTROLLER_IP):8443 \
	docker compose -f deploy/docker-compose.attacker-raw.yml up -d

deploy-all:
	@echo "=== Deploying full stack ==="
	SHARED_SECRET=$(SHARED_SECRET) \
	docker compose up -d --build

# ========== 工具 ==========
shell-controller:
	docker exec -it ddos-controller bash

logs-controller:
	docker logs -f ddos-controller

logs-attacker:
	docker logs -f ddos-attacker-http

# ========== 清理 ==========
clean:
	@echo "=== Cleaning build artifacts ==="
	rm -rf dist/ build/_*build/
	docker compose down -v 2>/dev/null || true
	@echo "Done."

# ========== 测试 (v1.5.0: 97 单元 + 4 E2E 套件) ==========
test: test-controller test-attacker
	@echo ""
	@echo "  [OK] 全部测试通过, 无 BUG"

test-controller:
	@echo ">>> Controller 测试 (16 套件)"
	cd controller && \
		SHARED_SECRET=$(SHARED_SECRET) \
		LOG_LEVEL=error \
		NODE_INSECURE_PLAIN_HTTP=true \
		NODE_PLAIN_HTTP_BANNED=false \
		CA_STORAGE_DIR=$(CURDIR)/.ca_test \
		python -m pytest tests/ -v --tb=short

test-attacker:
	@echo ">>> Attacker 测试 (2 套件)"
	cd attacker && \
		SHARED_SECRET=$(SHARED_SECRET) \
		LOG_LEVEL=error \
		ALLOWED_TARGET_CIDRS=10.100.0.0/16 \
		ALLOW_ANY_TARGET=false \
		python -m pytest tests/ -v --tb=short

test-coverage:
	@echo ">>> 覆盖率报告 (pytest-cov)"
	cd controller && \
		SHARED_SECRET=$(SHARED_SECRET) \
		LOG_LEVEL=error \
		NODE_INSECURE_PLAIN_HTTP=true \
		CA_STORAGE_DIR=$(CURDIR)/.ca_test \
		python -m pytest tests/ --cov=app --cov-report=term-missing --tb=short
	cd attacker && \
		SHARED_SECRET=$(SHARED_SECRET) \
		LOG_LEVEL=error \
		python -m pytest tests/ --cov=app --cov-report=term-missing --tb=short

# ========== Lint (v1.5.0 CI 门禁) ==========
lint:
	@echo ">>> ruff lint + format check"
	ruff check controller/ attacker/ build/
	ruff format --check controller/ attacker/ build/