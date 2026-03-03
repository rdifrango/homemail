.PHONY: install start stop restart status logs batch sync test-smb docker-build docker-up docker-down docker-logs help

PIPELINE_DIR := /opt/homemail/_pipeline
SERVICE      := homemail

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-14s %s\n", $$1, $$2}'

install: ## Full install (requires sudo)
	@test $$(id -u) -eq 0 || { echo "Run with sudo: sudo make install"; exit 1; }
	bash $(PIPELINE_DIR)/install.sh

start: ## Start the homemail service
	sudo systemctl start $(SERVICE)
	@echo "Started. Dashboard at http://$$(hostname -I | awk '{print $$1}'):8080/Reports/"

stop: ## Stop the homemail service
	sudo systemctl stop $(SERVICE)

restart: ## Restart the homemail service
	sudo systemctl restart $(SERVICE)

status: ## Show service status
	@systemctl status $(SERVICE) --no-pager || true

logs: ## Tail live journal logs
	journalctl -u $(SERVICE) -f

batch: ## One-shot batch processing of existing files
	uv run $(PIPELINE_DIR)/pipeline.py --batch -v

sync: ## Run OwnCloud sync manually
	bash $(PIPELINE_DIR)/sync.sh

test-smb: ## Verify Samba share is accessible
	@echo "Testing SMB share on localhost..."
	smbclient //localhost/HomeMail -U scanner -c "ls" && echo "OK" || echo "FAILED"

docker-build: ## Build the Docker image
	docker compose -f docker/docker-compose.yml build

docker-up: ## Start the container (builds if needed)
	docker compose -f docker/docker-compose.yml up -d --build

docker-down: ## Stop and remove the container
	docker compose -f docker/docker-compose.yml down

docker-logs: ## Tail container logs
	docker compose -f docker/docker-compose.yml logs -f
