.PHONY: help install install-dev lint format test test-cov test-all clean docker-build

help:  ## Show this help message
	@echo "Available targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-20s %s\n", $$1, $$2}'

install:  ## Install production dependencies
	pip install -r requirements.txt

install-dev:  ## Install development dependencies
	pip install -r requirements.txt
	pip install -r requirements-test.txt

lint:  ## Run linters
	@echo "Running ruff..."
	ruff check main.py tests/
	@echo "Running ruff format check..."
	ruff format --check main.py tests/
	@echo "Running mypy..."
	mypy main.py --ignore-missing-imports

format:  ## Format code with ruff
	ruff format main.py tests/
	ruff check --fix main.py tests/

test:  ## Run unit tests only (fast)
	pytest -m "not slow" -v

test-cov:  ## Run tests with coverage report
	pytest -v --cov --cov-report=term-missing:skip-covered --cov-report=html

test-all:  ## Run all tests including slow tests
	pytest -v

test-unit:  ## Run unit tests only
	pytest -m "unit" -v

test-integration:  ## Run integration tests only
	pytest -m "integration" -v

clean:  ## Clean up test artifacts
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".pytest_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name ".mypy_cache" -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "htmlcov" -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	rm -f coverage.xml .coverage 2>/dev/null || true
	rm -rf .ruff_cache 2>/dev/null || true

docker-build:  ## Build Docker image
	docker buildx build --platform linux/amd64 --load -t yt-dlp-api:test .

docker-run:  ## Run Docker container
	docker run -p 8000:8000 -e SERVER_OUTPUT_ROOT=/downloads -v $$PWD/downloads:/downloads yt-dlp-api:test

check: lint test-cov  ## Run all checks (lint + test)
	@echo "All checks passed!"
