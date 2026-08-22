.PHONY: all build docker-build docker-push binary binary-package certs deploy-controller deploy-attacker deploy-attacker-raw clean help

# =============================================================================
# DDoS Attack Platform — 一键构建/部署 Makefile
# =============================================================================

VERSION ?= $(shell git describe --tags --always --dirty 2>/dev/null || echo "dev")
REGISTRY ?= ghcr.io/your-org
CONTROLLER_IP ?= 10.100.1.10
SHARED_SECRET ?= $(shell openssl rand -hex 32 2>/dev/null || echo "changeme32charslongsecret")

all: certs docker-build

help:
	@echo "DDoS Attack Platform v$(VERSION)"
	@echo ""
	@echo "  Development:"
	@echo "    make certs               Generate mTLS certificates"
	@echo "    make docker-build        Build all Docker images locally"
	@echo "    make docker-push         Build & push to GHCR"
	@echo "    make binary              Build standalone Linux binaries"
	@echo "    make binary-package      Build and package as .tar.gz"
	@echo ""
	@echo "  Deployment:"
	@echo "    make deploy-controller   Deploy Controller via docker"
	@echo "    make deploy-attacker     Deploy HTTP Attacker via docker"
	@echo "    make deploy-attacker-raw Deploy RAW Attacker via docker"
	@echo "    make deploy-all          Deploy everything (single-machine)"
	@echo ""
	@echo "  Utilities:"
	@echo "    make clean               Remove all build artifacts"
	@echo "    make shell-controller    Shell into Controller container"
	@echo "    make logs-controller     Tail Controller logs"

# ========== 证书 ==========
certs:
	@echo "=== Generating mTLS certificates ==="
	cd deploy && \
	CONTROLLER_IP=$(CONTROLLER_IP) \
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