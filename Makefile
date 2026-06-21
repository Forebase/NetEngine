.PHONY: help install test lint format clean dev-up dev-down

help:
	@echo "NetEngine Development Tasks"
	@echo ""
	@echo "  make install    - Install dependencies with Poetry"
	@echo "  make test       - Run pytest suite"
	@echo "  make lint       - Run mypy, black, isort, flake8 checks"
	@echo "  make format     - Auto-format code with black and isort"
	@echo "  make clean      - Remove build artifacts and caches"
	@echo "  make dev-up     - Start dev environment (docker-compose)"
	@echo "  make dev-down   - Stop dev environment"

install:
	poetry install

test:
	poetry run pytest tests/ -v

test-spec:
	poetry run pytest tests/test_spec_parsing.py -v

test-cov:
	poetry run pytest tests/ --cov=netengine --cov-report=html -v

lint:
	poetry run mypy netengine --strict
	poetry run black --check netengine tests
	poetry run isort --check netengine tests
	poetry run flake8 netengine tests

format:
	poetry run black netengine tests
	poetry run isort netengine tests

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .coverage -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	find . -type f -name "*.pyo" -delete 2>/dev/null || true
	rm -rf dist build *.egg-info 2>/dev/null || true

dev-up:
	docker-compose -f docker-compose.dev.yaml up -d

dev-down:
	docker-compose -f docker-compose.dev.yaml down
